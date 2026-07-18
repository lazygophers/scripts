"""Git 工作流：同步 → 合并 → 推送到目标分支。"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .build import BuildError, check_build
from .exec import DEFAULT_TIMEOUT, NET_TIMEOUT, retry_command, run_logged
from .git import GitError, check_bit_clean, update_branch
from .notify import notify_via_n, project_done_message
from .ui import reporter


def _notify_done(suffix: str, *, script_dir: Path) -> None:
    """播报完成/失败语音。批量子进程模式（env _GITWF_BATCH=1）静默，由批量入口统一播报。"""
    if os.environ.get("_GITWF_BATCH") == "1":
        return
    notify_via_n(project_done_message(suffix), script_dir=script_dir)


_STEP_COUNTER = 0


def _step(msg: str, r) -> None:
    global _STEP_COUNTER
    _STEP_COUNTER += 1
    r.step(f"[{_STEP_COUNTER}] {msg}")


def _gate_check_build(r, *, where: str) -> None:
    """闸门：check_build 必须全绿才放行。

    check_build 只在 Go 路径抛 BuildError；Rust/Python(warn)/Java/C 失败仅返回 status=fail。
    此处统一兜底：任一 fail 即视为闸门失败，抛 GitError 中断工作流。
    """
    try:
        results = check_build(project_dir=Path("."), log=r.step)
    except BuildError as e:
        raise GitError(f"{where}构建检查失败: {e}") from e
    fails = [x for x in results if x.status == "fail"]
    if fails:
        detail = "; ".join(f"{x.name}: {x.message}" for x in fails if x.message)
        raise GitError(f"{where}构建检查失败: {detail}")


def _git(args: list[str], *, r=None, title: str = "", show_ok: bool = False, timeout: float | None = None):
    return run_logged(["git", *args], check=False, capture_output=True, r=r, title=title, show_output_on_success=show_ok, timeout=timeout)


def _remote_branch_exists(branch: str, *, remote: str = "origin") -> bool:
    p = _git(["ls-remote", "--exit-code", "--heads", remote, branch], timeout=NET_TIMEOUT)
    return p.returncode == 0


def _remote_head_branch(*, remote: str = "origin", r=None) -> str | None:
    p = _git(["symbolic-ref", "-q", "--short", f"refs/remotes/{remote}/HEAD"], r=r, title="远端默认分支")
    out = (p.stdout or "").strip()
    if not out:
        return None
    if "/" in out:
        return out.split("/", 1)[1]
    return out


def _ensure_remote_branch_exists(branch: str, *, remote: str = "origin", r=None) -> bool:
    if _remote_branch_exists(branch, remote=remote):
        return True

    base = _remote_head_branch(remote=remote, r=r)
    if not base or not _remote_branch_exists(base, remote=remote):
        # origin/HEAD 缺失或失效 → 枚举常见主分支名探真实存在
        base = next(
            (c for c in ("main", "master") if _remote_branch_exists(c, remote=remote)),
            "master",
        )

    if r is not None:
        r.warn(f"远端不存在 {remote}/{branch}，将自动创建（基于 {remote}/{base}）")

    # 本地已存在同名分支时不强制移动其 ref（避免丢失未推送的 commit），
    # 仅在本地缺失时基于 remote/base 创建。
    local_exists = _git(["rev-parse", "--verify", branch])
    if local_exists.returncode != 0:
        p1 = _git(["branch", branch, f"{remote}/{base}"], r=r, title="创建本地分支", show_ok=True)
        if p1.returncode != 0:
            return False
    elif r is not None:
        r.warn(f"本地已存在 {branch}，保留现有 ref 推送到远端")

    p2 = _git(["push", "-u", remote, branch], r=r, title="创建远端分支", show_ok=True)
    return p2.returncode == 0


def _preview_merge_conflicts(base: str, head: str, *, r=None) -> bool:
    """无副作用预演: base 合并 head 是否有冲突。

    用 `git merge-tree --write-tree`（Git ≥2.38，只算 tree 不动工作树/索引）。
    返回 True=有冲突, False=干净。git 过旧不支持 --write-tree 时降级为
    `git merge --no-commit --no-ff` + abort（有副作用风险，故仅作兜底）。
    """
    p = _git(["merge-tree", "--write-tree", "--name-only", base, head],
             r=r, title=f"预演合并 {head} → {base}")
    if p.returncode in (0, 1):
        # merge-tree: 0=干净合并, 1=有冲突
        return p.returncode == 1
    # 兜底: 旧 git 不认 --write-tree → 回退到 checkout + merge --no-commit + abort
    _step(f"git 版本不支持 merge-tree 预演，回退 --no-commit 探测 ({head} → {base})", r)
    probe = _git(["merge", "--no-commit", "--no-ff", head],
                 r=r, title="探测合并（将立即回滚）")
    has_conflict = probe.returncode != 0
    _git(["merge", "--abort"], r=r, title="回滚探测", show_ok=False)
    return has_conflict


def _resolve_target(
    target_arg: str | None,
    *,
    auto_detect: bool = False,
    r=None,
) -> tuple[str, str | None]:
    """解析目标分支。"""
    if not auto_detect:
        return (target_arg or "canary", None)

    default_branch = _remote_head_branch(r=r)
    if not default_branch:
        raise GitError("无法获取远端默认分支，请确保 origin 存在且可访问")
    return (target_arg or default_branch, default_branch)


def run_workflow(
    script_name: str,
    default_branch: str | None,
    argv: list[str],
    *,
    stay_on_target: bool = False,
) -> int:
    global _STEP_COUNTER
    _STEP_COUNTER = 0

    auto_detect = default_branch == "master"  # master 哨兵: 找真实 master/main

    parser = argparse.ArgumentParser(
        description=f"自动化 Git 工作流：同步 → 合并 → 推送到 {default_branch if not auto_detect else '远端默认分支'}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               f"  {script_name}           # 合并到 {default_branch if not auto_detect else '远端默认分支'}\n"
               f"  {script_name} --dry-run # 仅预览，不执行",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不执行实际操作")
    parser.add_argument("--auto-commit", action="store_true",
                        help="当前分支有未提交变更时，自动调 commit 提交后再继续工作流")
    parser.add_argument("--no-check", action="store_true",
                        help="跳过 checkwork 构建检查闸门（当前分支预检 + 合并结果检）")
    parser.add_argument("target_arg", nargs="?", default=default_branch if not auto_detect else None, help=f"目标分支（默认: {default_branch if not auto_detect else '远端默认分支'}）")
    parsed = parser.parse_args(argv[1:])

    r = reporter(stderr=True)
    script_dir = Path(__file__).resolve().parent

    resolved_target, default_hint = _resolve_target(
        parsed.target_arg,
        auto_detect=auto_detect,
        r=r,
    )
    target_branch = resolved_target

    if default_hint:
        r.kv("远端默认分支", {"远端 HEAD": default_hint})

    p = _git(["branch", "--show-current"], r=r, title="当前分支")
    current_branch = (p.stdout or "").strip()
    if p.returncode != 0 or not current_branch:
        r.err(f"无法获取当前分支: {(p.stdout or '') + (p.stderr or '')}".rstrip())
        return 1

    original_branch = current_branch

    r.panel(
        f"Git 自动化工作流：{script_name}",
        f"[cyan]当前分支[/cyan]  {current_branch}\n"
        f"[cyan]目标分支[/cyan]  {target_branch}\n"
        f"[cyan]工作目录[/cyan]  {Path('.').resolve()}",
        style="blue",
    )

    if current_branch == target_branch:
        r.warn(f"当前已是 {target_branch}，跳过操作")
        return 0

    if parsed.dry_run:
        r.rule("演练模式", style="yellow")
        steps = []
        if parsed.auto_commit:
            steps.append(("自动提交", "若有未提交变更，调 commit（lib）"))
        if not parsed.no_check:
            steps.append(("构建检查", f"checkwork 当前分支 + 合并结果"))
        steps += [
            ("同步分支", f"git pull + git push ({current_branch})"),
            ("合并", f"git merge {current_branch} → {target_branch}"),
            ("推送", f"git push origin {target_branch}"),
        ]
        if not stay_on_target:
            steps.append(("创建/更新目标分支", "git branch -f / git push -u (if needed)"))
            steps.append(("切回", f"git checkout {current_branch}"))
        for i, (name, detail) in enumerate(steps, 1):
            r.step(f"[{i}] {name}: {detail}")
        if parsed.no_check:
            r.warn("--no-check：已跳过 checkwork 构建检查闸门")
        r.ok("演练完成，无实际变更")
        return 0

    try:
        # --auto-commit：闸门前若有未提交变更，自动调 commit 提交（lib 直调，不经 bin 薄壳）
        if parsed.auto_commit:
            from lib.commit_wf import _has_changes, run_commit
            has, _ = _has_changes()
            if has:
                _step(f"检测到未提交变更，--auto-commit 自动提交 {current_branch}", r)
                rc = run_commit()
                if rc != 0:
                    raise GitError(f"自动提交失败（退出码 {rc}），中止工作流")

        # 闸门1：切目标分支前，当前分支必须 build 通过（防止把会 break 的代码合进目标分支）。
        # 与 push 前的第二道闸（合并后 check）互补：此处在源分支拦截，彼处在合并结果上拦截。
        if parsed.no_check:
            _step(f"跳过当前分支 {current_branch} 构建检查（--no-check）", r)
        else:
            _step(f"预检：当前分支 {current_branch} 构建检查", r)
            check_bit_clean()
            _gate_check_build(r, where=f"当前分支 {current_branch} ")

        _step(f"同步当前分支 {current_branch}", r)
        update_branch(current_branch, r=r)

        if not _ensure_remote_branch_exists(target_branch, r=r):
            raise GitError(f"自动创建 {target_branch} 分支失败（可能无推送权限或网络问题）")

        _step(f"同步目标分支 {target_branch}", r)
        # 目标分支 checkout 后工作区可能因 gitignore/行尾残留显示"脏"但无实质改动，
        # 此处不 check；合并后的干净度检查在 merge 完成后统一做（下方 check_bit_clean）。
        update_branch(target_branch, r=r, check_after_pull=False)

        _step(f"预演合并 {current_branch} → {target_branch}（无副作用）", r)
        if _preview_merge_conflicts(target_branch, current_branch, r=r):
            r.err(f"预演发现合并冲突，中止操作（未执行实际合并）")
            r.warn("请先在本地解决冲突后重新运行")
            _git(["checkout", original_branch], r=r, title="回滚分支")
            _notify_done("预演发现冲突，未执行", script_dir=script_dir)
            raise GitError("预演发现合并冲突，操作已中止")

        _step(f"合并 {current_branch} → {target_branch}", r)
        merge = _git(["merge", "--no-edit", current_branch], r=r, title="执行合并", show_ok=True, timeout=DEFAULT_TIMEOUT)
        if merge.returncode != 0:
            if sys.stdin.isatty():
                r.warn("检测到合并冲突：请手动解决后按回车继续")
                input()
                _git(["add", "."], r=r, title="标记所有冲突已解决")
                cont = _git(["commit", "--no-edit"], r=r, title="完成合并提交", show_ok=True)
            else:
                r.err("检测到合并冲突：非交互模式下无法继续，请手动解决后重新运行")
                _git(["checkout", original_branch], r=r, title="回滚分支")
                _notify_done("合并冲突未解决", script_dir=script_dir)
                raise GitError("合并冲突未解决")
            if cont.returncode != 0:
                _git(["checkout", original_branch], r=r, title="回滚分支")
                _notify_done("合并冲突未解决", script_dir=script_dir)
                raise GitError("冲突未完全解决，操作已终止！")

        check_bit_clean()
        if parsed.no_check:
            _step(f"跳过合并结果({target_branch}) 构建检查（--no-check）", r)
        else:
            _gate_check_build(r, where=f"合并结果({target_branch}) ")

        _step(f"推送 {target_branch} 到远端", r)
        sync = retry_command(["git", "push", "origin", target_branch], max_retries=3, timeout=NET_TIMEOUT)
        if not sync.ok:
            r.err("推送失败！请检查网络或权限。")
            r.cmd_result(
                ["git", "push", "origin", target_branch],
                returncode=1,
                output=sync.last_output,
                show_output=True,
                title="push 输出",
            )
            if not stay_on_target:
                _git(["checkout", original_branch], r=r, title="回到原始分支")
            _notify_done("推送失败", script_dir=script_dir)
            raise GitError("推送失败！请检查网络或权限。")
        if sync.last_output.strip():
            r.output(sync.last_output)

        if stay_on_target:
            r.panel(
                "工作流完成",
                f"{current_branch}  →  {target_branch}\n"
                f"推送成功  ·  留在 {target_branch}",
                style="green",
            )
        else:
            _step(f"切回原始分支 {original_branch}", r)
            _git(["checkout", original_branch], r=r, title="切换分支")

            r.panel(
                "工作流完成",
                f"{current_branch}  →  {target_branch}\n"
                f"推送成功  ·  切回 {original_branch}",
                style="green",
            )

        _notify_done("Git 工作流完成", script_dir=script_dir)
        return 0

    except GitError as e:
        r.err(str(e))
        return 1
    finally:
        if not stay_on_target:
            cur = _git(["branch", "--show-current"])
            if (cur.stdout or "").strip() != original_branch:
                _git(["checkout", original_branch], r=r, title="兜底切回原始分支", show_ok=True)


def run_merge_workflow(
    script_name: str,
    default_branch: str | None,
    argv: list[str],
) -> int:
    """merge_* 流程：把目标分支 merge 到当前分支（方向 target → current）。

    5 步：
    1. 本地工作区干净检查
    2. 更新当前分支为最新（远端无则跳过）
    3. 更新目标分支为最新
    4. 预演 target → current 冲突，有冲突问用户是否停止
    5. 执行 merge target 到 current

    与 push_*（current → target + push）方向相反，故独立流程不复用 run_workflow。
    """
    global _STEP_COUNTER
    _STEP_COUNTER = 0

    auto_detect = default_branch == "master"
    parser = argparse.ArgumentParser(
        description=f"合并目标分支到当前分支（target → current）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               f"  {script_name}           # 合并 {default_branch if not auto_detect else '远端默认分支'} → 当前\n"
               f"  {script_name} --dry-run # 仅预览",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不执行实际操作")
    parser.add_argument("--auto-commit", action="store_true",
                        help="当前分支有未提交变更时，自动调 commit 提交后再继续")
    parser.add_argument("target_arg", nargs="?", default=default_branch if not auto_detect else None,
                        help=f"目标分支（将被 merge 到当前分支；默认: {default_branch if not auto_detect else '远端默认分支'}）")
    parsed = parser.parse_args(argv[1:])

    from lib.ui import ask_confirm

    r = reporter(stderr=True)
    script_dir = Path(__file__).resolve().parent

    resolved_target, default_hint = _resolve_target(
        parsed.target_arg,
        auto_detect=auto_detect,
        r=r,
    )
    target_branch = resolved_target
    if default_hint:
        r.kv("远端默认分支", {"远端 HEAD": default_hint})

    p = _git(["branch", "--show-current"], r=r, title="当前分支")
    current_branch = (p.stdout or "").strip()
    if p.returncode != 0 or not current_branch:
        r.err(f"无法获取当前分支: {(p.stdout or '') + (p.stderr or '')}".rstrip())
        return 1

    r.panel(
        f"Git 合并工作流：{script_name}",
        f"[cyan]当前分支[/cyan]  {current_branch}\n"
        f"[cyan]目标分支[/cyan]  {target_branch}  →  merge 到当前",
        style="blue",
    )

    if current_branch == target_branch:
        r.warn(f"当前已是 {target_branch}，跳过操作")
        return 0

    if parsed.dry_run:
        r.rule("演练模式", style="yellow")
        steps = []
        if parsed.auto_commit:
            steps.append(("自动提交", "若有未提交变更，调 commit（lib）"))
        steps += [
            ("干净检查", "check_bit_clean"),
            ("更新当前分支", f"git pull/push {current_branch}"),
            ("更新目标分支", f"git pull {target_branch}"),
            ("预演冲突", f"target {target_branch} → current {current_branch}"),
            ("合并", f"git merge {target_branch} → {current_branch}"),
        ]
        for i, (name, detail) in enumerate(steps, 1):
            r.step(f"[{i}] {name}: {detail}")
        r.ok("演练完成，无实际变更")
        return 0

    try:
        # 步骤0：--auto-commit 先处理未提交变更
        if parsed.auto_commit:
            from lib.commit_wf import _has_changes, run_commit
            has, _ = _has_changes()
            if has:
                _step(f"检测到未提交变更，--auto-commit 自动提交 {current_branch}", r)
                rc = run_commit()
                if rc != 0:
                    raise GitError(f"自动提交失败（退出码 {rc}），中止工作流")

        # 步骤1：本地工作区干净检查
        _step(f"检查工作区是否干净", r)
        check_bit_clean()

        # 步骤2：更新当前分支为最新（远端无该分支则跳过）
        if _remote_branch_exists(current_branch):
            _step(f"更新当前分支 {current_branch}（pull + push）", r)
            update_branch(current_branch, r=r)
        else:
            r.warn(f"远端无 {current_branch}，跳过同步当前分支")

        # 步骤3：更新目标分支为最新
        if not _ensure_remote_branch_exists(target_branch, r=r):
            raise GitError(f"自动创建 {target_branch} 分支失败（可能无推送权限或网络问题）")
        _step(f"更新目标分支 {target_branch}（checkout 后 pull，不查未提交）", r)
        update_branch(target_branch, r=r, check_after_pull=False)

        # 切回当前分支（update_branch 可能停在 target 上）
        p = _git(["branch", "--show-current"])
        if (p.stdout or "").strip() != current_branch:
            _git(["checkout", current_branch], r=r, title="切回当前分支")

        # 步骤4：预演 target → current 冲突，有冲突问用户是否停止
        _step(f"预演合并 {target_branch} → {current_branch}（无副作用）", r)
        if _preview_merge_conflicts(current_branch, target_branch, r=r):
            r.warn(f"预演发现合并冲突：{target_branch} → {current_branch}")
            stop = ask_confirm("存在冲突，是否停止（不 merge）？", default=True)
            if stop is None or stop:
                r.err("用户选择停止，未执行 merge")
                _notify_done("预演发现冲突，用户停止", script_dir=script_dir)
                raise GitError("预演发现冲突，用户选择停止")
            r.warn("用户选择继续，将执行 merge（可能产生冲突需手动解决）")

        # 步骤5：执行 merge target 到 current
        _step(f"合并 {target_branch} → {current_branch}", r)
        merge = _git(["merge", "--no-edit", target_branch], r=r, title="执行合并", show_ok=True, timeout=DEFAULT_TIMEOUT)
        if merge.returncode != 0:
            if sys.stdin.isatty():
                r.warn("检测到合并冲突：请手动解决后按回车继续")
                input()
                _git(["add", "."], r=r, title="标记所有冲突已解决")
                cont = _git(["commit", "--no-edit"], r=r, title="完成合并提交", show_ok=True)
            else:
                r.err("检测到合并冲突：非交互模式下无法继续，请手动解决后重新运行")
                _git(["merge", "--abort"], r=r, title="回滚合并")
                _notify_done("合并冲突未解决", script_dir=script_dir)
                raise GitError("合并冲突未解决")
            if cont.returncode != 0:
                _git(["merge", "--abort"], r=r, title="回滚合并")
                _notify_done("合并冲突未解决", script_dir=script_dir)
                raise GitError("冲突未完全解决，操作已终止！")

        r.panel(
            "合并完成",
            f"{target_branch}  →  {current_branch}\n"
            f"留在 {current_branch}",
            style="green",
        )
        _notify_done("merge 工作流完成", script_dir=script_dir)
        return 0

    except GitError as e:
        r.err(str(e))
        return 1


def merge_to(target: str, argv: list[str] | None = None) -> int:
    """把目标分支 merge 到当前分支（target → current），留在当前分支。

    供薄壳 (bin/merge_canary 等) 直接调用。方向与 push_* 相反，走 run_merge_workflow。
    """
    if argv is None:
        argv = sys.argv
    script_name = os.environ.get("_SCRIPT_NAME", f"merge-{target}")
    passthrough = [target, *argv[1:]]
    return run_merge_workflow(script_name, target, passthrough)


def push_to(target: str, argv: list[str] | None = None) -> int:
    """合并当前分支 → target 并推送, 完成后切回原分支 (除非 --stay)。

    供薄壳 (bin/push_canary 等) 直接调用。
    """
    if argv is None:
        argv = sys.argv
    script_name = os.environ.get("_SCRIPT_NAME", f"push-{target}")
    passthrough = [target, *argv[1:]]
    stay = "--stay" in argv
    return run_workflow(script_name, target, passthrough, stay_on_target=stay)

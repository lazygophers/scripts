"""Git 工作流：同步 → 合并 → 推送到目标分支。"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .exec import retry_command, run_logged
from .git import GitError, check_bit_clean, update_branch
from .notify import notify_via_n, project_done_message
from .ui import reporter
from .build import check_build


_STEP_COUNTER = 0


def _step(msg: str, r) -> None:
    global _STEP_COUNTER
    _STEP_COUNTER += 1
    r.step(f"[{_STEP_COUNTER}] {msg}")


def _git(args: list[str], *, r=None, title: str = "", show_ok: bool = False):
    return run_logged(["git", *args], check=False, capture_output=True, r=r, title=title, show_output_on_success=show_ok)


def _remote_branch_exists(branch: str, *, remote: str = "origin") -> bool:
    p = _git(["ls-remote", "--exit-code", "--heads", remote, branch])
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
        base = "master"

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

    auto_detect = default_branch == "auto"

    parser = argparse.ArgumentParser(
        description=f"自动化 Git 工作流：同步 → 合并 → 推送到 {default_branch if not auto_detect else '远端默认分支'}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               f"  {script_name}           # 合并到 {default_branch if not auto_detect else '远端默认分支'}\n"
               f"  {script_name} --dry-run # 仅预览，不执行",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不执行实际操作")
    parser.add_argument("target_arg", nargs="?", default=default_branch if not auto_detect else None, help=f"目标分支（默认: {default_branch if not auto_detect else '远端默认分支'}）")
    parsed = parser.parse_args(argv[1:])

    r = reporter(stderr=True)
    script_dir = Path(__file__).resolve().parent

    r.rule("Git 自动化工作流", style="blue")

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

    r.kv("任务概览", {"当前分支": current_branch, "目标分支": target_branch, "工作目录": str(Path(".").resolve())})

    if current_branch == target_branch:
        r.warn(f"当前已是 {target_branch}，跳过操作")
        return 0

    if parsed.dry_run:
        r.rule("演练模式", style="yellow")
        steps = [
            ("同步分支", f"git pull + git push ({current_branch})"),
            ("合并", f"git merge {current_branch} → {target_branch}"),
            ("推送", f"git push origin {target_branch}"),
        ]
        if not stay_on_target:
            steps.append(("创建/更新目标分支", f"git branch -f / git push -u (if needed)"))
            steps.append(("切回", f"git checkout {current_branch}"))
        for i, (name, detail) in enumerate(steps, 1):
            r.step(f"[{i}] {name}: {detail}")
        r.ok("演练完成，无实际变更")
        return 0

    try:
        _step(f"同步当前分支 {current_branch}", r)
        update_branch(current_branch, r=r)

        if not _ensure_remote_branch_exists(target_branch, r=r):
            raise GitError(f"自动创建 {target_branch} 分支失败（可能无推送权限或网络问题）")

        _step(f"同步目标分支 {target_branch}", r)
        update_branch(target_branch, r=r)

        _step(f"合并 {current_branch} → {target_branch}", r)
        merge = _git(["merge", "--no-edit", current_branch], r=r, title="执行合并", show_ok=True)
        if merge.returncode != 0:
            if sys.stdin.isatty():
                r.warn("检测到合并冲突：请手动解决后按回车继续")
                input()
                _git(["add", "."], r=r, title="标记所有冲突已解决")
                cont = _git(["commit", "--no-edit"], r=r, title="完成合并提交", show_ok=True)
            else:
                r.err("检测到合并冲突：非交互模式下无法继续，请手动解决后重新运行")
                _git(["checkout", original_branch], r=r, title="回滚分支")
                notify_via_n(project_done_message("合并冲突未解决"), script_dir=script_dir)
                raise GitError("合并冲突未解决")
            if cont.returncode != 0:
                _git(["checkout", original_branch], r=r, title="回滚分支")
                notify_via_n(project_done_message("合并冲突未解决"), script_dir=script_dir)
                raise GitError("冲突未完全解决，操作已终止！")

        check_bit_clean()
        check_build(project_dir=Path("."), log=r.step)

        _step(f"推送 {target_branch} 到远端", r)
        sync = retry_command(["git", "push", "origin", target_branch], max_retries=3)
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
            notify_via_n(project_done_message("推送失败"), script_dir=script_dir)
            raise GitError("推送失败！请检查网络或权限。")
        if sync.last_output.strip():
            r.output(sync.last_output)

        if stay_on_target:
            r.rule("执行结果", style="green")
            r.summary("工作流完成", [
                ("从", f"{current_branch} → {target_branch}", "yellow"),
                ("推送", "成功", "green"),
                ("留在", target_branch, "cyan"),
            ])
        else:
            _step(f"切回原始分支 {original_branch}", r)
            _git(["checkout", original_branch], r=r, title="切换分支")

            r.rule("执行结果", style="green")
            r.summary("工作流完成", [
                ("从", f"{current_branch} → {target_branch}", "yellow"),
                ("推送", "成功", "green"),
                ("切回", original_branch, "cyan"),
            ])

        notify_via_n(project_done_message("Git 工作流完成"), script_dir=script_dir)
        return 0

    except GitError as e:
        r.err(str(e))
        return 1
    finally:
        if not stay_on_target:
            cur = _git(["branch", "--show-current"])
            if (cur.stdout or "").strip() != original_branch:
                _git(["checkout", original_branch], r=r, title="兜底切回原始分支", show_ok=True)


def merge_to(target: str, argv: list[str] | None = None) -> int:
    """合并当前分支 → target, 留在 target。供薄壳 (bin/mergec 等) 直接调用。"""
    if argv is None:
        argv = sys.argv
    script_name = os.environ.get("_SCRIPT_NAME", f"merge-{target}")
    passthrough = [target, *argv[1:]]
    return run_workflow(script_name, target, passthrough, stay_on_target=True)


def push_to(target: str, argv: list[str] | None = None) -> int:
    """合并当前分支 → target 并推送, 完成后切回原分支 (除非 --stay)。

    供薄壳 (bin/pushc 等) 直接调用。
    """
    if argv is None:
        argv = sys.argv
    script_name = os.environ.get("_SCRIPT_NAME", f"push-{target}")
    passthrough = [target, *argv[1:]]
    stay = "--stay" in argv
    return run_workflow(script_name, target, passthrough, stay_on_target=stay)

"""批量 Git 仓库操作公共库。

提供 Git 仓库扫描、批量执行、汇总报告等功能。
"""

from __future__ import annotations

import io
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from lib.ui import Reporter, print_ansi, progress, reporter


@dataclass
class RepoResult:
    """单个仓库的执行结果。"""
    name: str
    path: str
    status: str  # "ok" | "skip" | "fail"
    detail: str = ""


@dataclass
class BatchResult:
    """批量操作汇总。"""
    total: int = 0
    succeeded: list[RepoResult] = field(default_factory=list)
    skipped: list[RepoResult] = field(default_factory=list)
    failed: list[RepoResult] = field(default_factory=list)


def scan_repos(root: Path, *, max_depth: int = 3) -> list[Path]:
    """扫描目录下所有 Git 仓库（含 .git 的目录，不限 remote 提供商）。"""
    repos: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        # relative_to(root) 在 root 本身为 '.', 子目录为 'a', 'a/b'...
        # 路径组件数 = sep 数 + 1; max_depth 按组件数计 (root 下第 N 层 = N 个组件)
        rel = Path(dirpath).relative_to(root)
        depth = 0 if str(rel) == "." else len(rel.parts)
        if depth >= max_depth:
            dirnames.clear()
            continue
        # .git 可能是目录（普通仓库）或文件（submodule / worktree 的 gitdir 指针）
        if ".git" in dirnames or ".git" in filenames:
            repos.append(Path(dirpath))
        if ".git" in dirnames:
            # 避免 os.walk 误入 .git 目录内部
            dirnames.remove(".git")
    repos.sort(key=lambda p: str(p.relative_to(root)))
    return repos


# 旧名保留，向后兼容（历史名 scan_gitlab_repos，现已不限 GitLab）
scan_gitlab_repos = scan_repos


def print_repo_list(r: Reporter, repos: list[Path], root: Path) -> None:
    """打印仓库列表（保留向后兼容；run_batch 现已改用单行扫描摘要，不再调用）。"""
    for repo in repos:
        r.info(f"  •  {repo.relative_to(root)}")


def print_summary(
    r: Reporter,
    title: str,
    result: BatchResult,
) -> None:
    """打印批量操作汇总（紧凑 Table：仓库/状态/详情 + 单行 footer 统计）。"""
    items: list[tuple[str, str, str]] = (
        [(x.name, "ok", x.detail) for x in result.succeeded]
        + [(x.name, "skip", x.detail) for x in result.skipped]
        + [(x.name, "fail", x.detail) for x in result.failed]
    )
    r.status_table(title, items)
    # 单行 footer：失败(红) · 成功(绿) · 跳过(黄)，各数字按状态色
    parts: list[tuple[str, str]] = []
    if result.failed:
        parts.append((f"失败 {len(result.failed)}/{result.total}", "red"))
    if result.succeeded:
        parts.append((f"成功 {len(result.succeeded)}/{result.total}", "green"))
    if result.skipped:
        parts.append((f"跳过 {len(result.skipped)}/{result.total}", "yellow"))
    if not parts:
        parts.append((f"共 {result.total} 个", "cyan"))
    r.status_footer(parts)


def notify_batch_done(folder_name: str, result: BatchResult, *, script_dir: Path) -> None:
    """批量操作完成通知 — 文案按 succeeded/skipped/failed 精确组合，避免误导。

    全 skip（如 delete_branch 删不存在的分支）说"全部跳过"而非"完成"。
    """
    from lib.notify import notify_via_n
    s, k, f = len(result.succeeded), len(result.skipped), len(result.failed)
    if f and s:
        parts = [f"成功 {s}"]
        if k:
            parts.append(f"跳过 {k}")
        parts.append(f"失败 {f}")
        msg = f"{folder_name} 部分失败：" + "、".join(parts)
    elif f:
        msg = f"{folder_name} 失败 {f} 个" + (f"、跳过 {k}" if k else "")
    elif s and k:
        msg = f"{folder_name} 成功 {s}、跳过 {k}"
    elif s:
        msg = f"{folder_name} 成功 {s} 个"
    elif k:
        msg = f"{folder_name} 全部跳过（{k} 个）"
    else:
        msg = f"{folder_name} 无仓库可处理"
    notify_via_n(msg, script_dir=script_dir)


# type alias for the per-repo operation callback
# operation(repo, r, root) → (status, detail) where status ∈ {"ok", "skip", "fail"}
OperationFn = Callable[[Path, Reporter, Path], tuple[str, str]]


def run_batch(
    title: str,
    root: Path,
    operation: OperationFn,
    *,
    folder_name: str | None = None,
    script_dir: Path | None = None,
    confirm: bool = True,
) -> BatchResult:
    """批量仓库操作公共流程（并行）。

    扫描仓库 → 确认 → ThreadPoolExecutor 并行执行（per-repo buffer，
    完成即 flush，避免多线程 Rich 输出交错）→ 汇总 → 通知。

    Args:
        title: 规则标题
        root: 仓库根目录
        operation: 每个仓库的操作函数 (repo, r, root) → (status, detail)
        folder_name: 通知用目录名（默认为 root.name）
        script_dir: 脚本目录（用于通知）
        confirm: 是否需要用户确认
    """
    r = reporter(stderr=True)
    if folder_name is None:
        folder_name = root.name
    if script_dir is None:
        script_dir = Path(__file__).resolve().parent.parent

    r.rule(title, style="blue")
    repos = scan_repos(root)
    concurrency = max(1, int(os.environ.get("BATCH_CONCURRENCY", "4")))
    # 单行扫描摘要（禁逐行列仓库，避免与汇总段重复）
    r.info(f"扫描 {len(repos)} 个仓库（并发 {concurrency}）")

    if confirm and os.environ.get("BATCH_NO_CONFIRM") != "1":
        # 非 TTY（cron/管道/CI）下无法交互确认 → fail-closed：要求显式 -y / BATCH_NO_CONFIRM=1
        if not sys.stdin.isatty():
            r.err("批量删除需交互确认，但当前非 TTY。加 -y 或 BATCH_NO_CONFIRM=1 显式放行。")
            raise SystemExit(1)
        try:
            answer = input("\n确认执行？(y/N) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in ("y", "yes"):
            r.warn("已取消")
            raise SystemExit(0)

    result = BatchResult(total=len(repos))

    def _run_one(idx: int, repo: Path) -> tuple[int, str, RepoResult]:
        """单仓库在线程内执行：写 per-repo buffer，返回 (idx, buf, RepoResult)。"""
        rel = repo.relative_to(root)
        buf = io.StringIO()
        rr_per_repo = Reporter.from_buffer(buf)
        try:
            status, detail = operation(repo, rr_per_repo, root)
            rr = RepoResult(name=str(rel), path=str(repo), status=status, detail=detail)
        except Exception as e:
            rr_per_repo.err(f"异常: {e}")
            rr = RepoResult(name=str(rel), path=str(repo), status="fail", detail=str(e))
        return idx, buf.getvalue(), rr

    # Progress 进度条：best-effort（无 rich 退回裸 stderr flush）。
    # per-repo buffer 防交错优先于进度条视觉；进度条与日志共存靠 Live 重定向。
    prog = progress(r.console)
    prog_task = None
    if prog is not None:
        prog_task = prog.add_task("处理中", total=len(repos))
        prog.start()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(_run_one, idx, repo): (idx, repo)
            for idx, repo in enumerate(repos)
        }
        try:
            for fut in as_completed(futures):
                idx, buf_text, rr = fut.result()
                if prog is not None and prog_task is not None:
                    prog.advance(prog_task)
                    prog.update(prog_task, description=f"处理中 {rr.name}")
                # 先 flush 该仓库整段日志（顺序完整不交错），再追状态行
                if buf_text:
                    if prog is not None:
                        # Live 激活时走 console.print，让 Rich 在进度条上方渲染
                        if not print_ansi(prog.console, buf_text):
                            sys.stderr.write(buf_text)
                    else:
                        sys.stderr.write(buf_text)
                # 单图标状态行（去双图标：✓/•/✗ 单 icon + 仓库名 + 详情）
                name = rr.name
                line = f"{name}{(' — ' + rr.detail) if rr.detail else ''}"
                r.status(rr.status, line)
                if rr.status == "ok":
                    result.succeeded.append(rr)
                elif rr.status == "skip":
                    result.skipped.append(rr)
                else:
                    result.failed.append(rr)
        except KeyboardInterrupt:
            # ponytail: os._exit 跳过 with 的 shutdown(wait=True) + 解释器 _python_exit join。
            # worker 不可中断（git 子进程在跑），cancel_futures 只取消未启动 future；
            # 不强退会死等 worker 跑完整 fetch/push，表现为"中断后卡住"。
            r.warn("\n用户中断，停止执行")
            if prog is not None:
                prog.stop()
            os._exit(130)
        finally:
            if prog is not None:
                prog.stop()

    print_summary(r, "执行结果", result)
    notify_batch_done(folder_name, result, script_dir=script_dir)
    return result


# ── 三个具体批量操作的薄壳入口 ─────────────────────────────────────────
# 用 closure 捕获参数，替代旧实现的模块级 _TARGET/_FORCE/_DRY_RUN/_EXTRA 全局态。

from lib.exec import run as _run  # noqa: E402, I001
from lib.git import get_current_branch as _get_current_branch  # noqa: E402


_ERROR_PATTERN = re.compile(
    r"conflict|rejected|fatal|error:|denied|timeout|unresolved|diverged|non-fast-forward",
    re.IGNORECASE,
)


def _extract_error(out: str, code: int, label: str) -> str:
    """从子进程输出提取关键错误行（单行，≤200 字符）。

    优先返回最后一个匹配错误关键词的行；否则最后非空行；否则 fallback。
    """
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    matched = [ln for ln in lines if _ERROR_PATTERN.search(ln)]
    if matched:
        return matched[-1][:200]
    if lines:
        return lines[-1][:200]
    return f"{label} 失败 (exit {code})"


def _dirty_detail(repo: Path) -> str:
    """构造「工作区有未提交改动」detail，附前若干个脏文件名（≤200 字符）。"""
    p = _run(["git", "status", "--porcelain"], cwd=str(repo), check=False, capture_output=True)
    lines = [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]
    n = len(lines)
    sample = ", ".join(ln.split(maxsplit=1)[-1] for ln in lines[:3])
    if n == 0:
        return ""
    detail = f"工作区有未提交改动（{n} 项）"
    if sample:
        detail += f": {sample}"
        if n > 3:
            detail += f" …(+{n - 3})"
    return detail[:200]


def _push_one_factory(target: str, dry_run: bool, extra: list[str]) -> OperationFn:
    """构造 push 单仓库操作（捕获 target/dry_run/extra）。

    单仓执行调新名 symlink `push_{target}`（需在 PATH 中可见）。
    """
    def _op(repo: Path, r: Reporter, _root: Path) -> tuple[str, str]:
        # fetch
        r.step("fetch origin …")
        p = _run(["git", "fetch", "origin"], cwd=str(repo), check=False, capture_output=True)
        if p.returncode != 0:
            err = ((p.stdout or '') + (p.stderr or '')).strip()
            r.err(f"fetch origin 失败: {err[:200]}")
            return "fail", f"fetch origin 失败: {err[:120]}"

        ref_check = _run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{target}"],
            cwd=str(repo), check=False, capture_output=True,
        )
        remote_target_exists = ref_check.returncode == 0

        current_branch = _get_current_branch(cwd=str(repo))
        if not current_branch:
            return "skip", "无法获取当前分支（detached HEAD）"
        r.info(f"当前分支: {current_branch}")

        # 条件1：当前分支相对远端 target 有新 commit
        cond1 = False
        cond1_reason = ""
        if not remote_target_exists:
            r.ok(f"条件1 通过 — 远端 {target} 不存在，视为有差异")
            cond1 = True
        else:
            log_p = _run(
                ["git", "log", f"origin/{target}..HEAD", "--oneline"],
                cwd=str(repo), check=False, capture_output=True,
            )
            commits = (log_p.stdout or "").strip()
            if commits:
                count = len(commits.splitlines())
                r.ok(f"条件1 通过 — {count} 个新 commit 待合并")
                for line in commits.splitlines():
                    r.info(f"       {line}")
                cond1 = True
            else:
                cond1_reason = f"当前分支相对 origin/{target} 无新 commit"
                # HEAD 虽追平, 但工作区可能脏（未 commit 改动）—— 探测并附进 reason,
                # 避免静默 skip 掩盖「仓库不是空的」的真实改动
                dirty = _dirty_detail(repo)
                if dirty:
                    cond1_reason = f"{cond1_reason}（但{dirty}）"
                r.info(f"条件1 不满足 — {cond1_reason}")

        # 条件2：本地 target 相对远端 target 有差异
        cond2 = False
        cond2_reason = ""
        local_target = _run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{target}"],
            cwd=str(repo), check=False, capture_output=True,
        )
        if not remote_target_exists and local_target.returncode == 0:
            r.ok(f"条件2 通过 — 远端 {target} 不存在但本地 {target} 存在")
            cond2 = True
        elif local_target.returncode == 0:
            diff_p = _run(
                ["git", "log", f"origin/{target}..{target}", "--oneline"],
                cwd=str(repo), check=False, capture_output=True,
            )
            diff_commits = (diff_p.stdout or "").strip()
            if diff_commits:
                count = len(diff_commits.splitlines())
                r.ok(f"条件2 通过 — 本地 {target} 领先远端 {count} 个 commit")
                for line in diff_commits.splitlines():
                    r.info(f"       {line}")
                cond2 = True
            else:
                cond2_reason = f"本地 {target} 与远端同步"
                r.info(f"条件2 不满足 — {cond2_reason}")

        if not cond1 and not cond2:
            if not cond2_reason and local_target.returncode != 0:
                cond2_reason = f"无本地 {target} 分支"
            reasons = [r for r in (cond1_reason, cond2_reason) if r]
            return "skip", "；".join(reasons) or "两个条件均不满足"

        if dry_run:
            return "ok", f"条件满足（dry-run 模式，不执行 push_{target}）"

        r.step(f"执行 push_{target} …")
        # 批量模式：子进程 run_workflow 不单独播报，由本入口收尾统一播报
        p = _run([f"push_{target}", *extra], cwd=str(repo), check=False, capture_output=True,
                 env={**os.environ, "_GITWF_BATCH": "1"})
        if p.returncode == 0:
            return "ok", ""
        out = (p.stdout or "") + (p.stderr or "")
        # 失败时流式打印子进程完整输出（info 降级），让用户看 gitc 上下文
        if out.strip():
            r.warn(f"push_{target} 子进程输出：")
            for ln in out.splitlines():
                if ln.strip():
                    r.info(f"  {ln}")
        return "fail", _extract_error(out, p.returncode, f"push_{target}")

    return _op


def push_all(
    target: str,
    argv: list[str] | None = None,
) -> int:
    """批量 push 到 target：扫描 Git 仓库，逐个执行 push_{target}。

    解析 --dry-run；其余参数透传给单仓 push_{target}。
    批量模式自动执行，无确认门（confirm=False）。
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog=f"push_{target}",
        description=f"批量 push 到 {target}：扫描 Git 仓库，逐个执行 push_{target}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="仅检查条件并预览仓库列表，不执行 push")
    parsed, extra = parser.parse_known_args(argv[1:] if argv is not None else None)

    result = run_batch(
        title=f"push_{target} 批量推送",
        root=Path(".").resolve(),
        operation=_push_one_factory(target, parsed.dry_run, extra),
        confirm=False,
    )
    return 1 if result.failed else 0


def _switch_one_factory(target: str) -> OperationFn:
    """构造 switch_branch 单仓库操作（捕获 target）。"""
    def _op(repo: Path, r: Reporter, _root: Path) -> tuple[str, str]:
        r.step("fetch origin …")
        p = _run(["git", "fetch", "origin"], cwd=str(repo), check=False, capture_output=True)
        if p.returncode != 0:
            r.warn(f"fetch 失败: {(p.stdout or '')[:200]}")

        current = _get_current_branch(cwd=str(repo))
        if current == target:
            r.ok(f"已在 {target} → 跳过")
            return "skip", "已在目标分支"

        # 脏工作树 → fail（不自动 stash，防丢失上下文）
        diff_p = _run(["git", "diff", "--quiet", "HEAD"], cwd=str(repo), check=False, capture_output=True)
        if diff_p.returncode != 0:
            err = _dirty_detail(repo)
            r.err(f"未提交变更 → 跳过: {err}")
            return "fail", err

        switched = False
        fail_err = ""
        local_check = _run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{target}"],
            cwd=str(repo), check=False, capture_output=True,
        )
        if local_check.returncode == 0:
            r.step(f"本地分支 {target} 已存在 → switch")
            sw = _run(["git", "switch", target], cwd=str(repo), check=False, capture_output=True)
            if sw.returncode == 0:
                r.ok(f"切换到 {target}")
                switched = True
            else:
                fail_err = _extract_error((sw.stderr or "") + (sw.stdout or ""), sw.returncode, "切换失败")
                r.err(f"切换失败: {fail_err}")
        else:
            remote_check = _run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{target}"],
                cwd=str(repo), check=False, capture_output=True,
            )
            if remote_check.returncode == 0:
                r.step(f"远端分支 origin/{target} 存在 → track & switch")
                sw = _run(["git", "switch", "-c", target, f"origin/{target}"],
                          cwd=str(repo), check=False, capture_output=True)
                if sw.returncode == 0:
                    r.ok(f"追踪并切换到 {target}")
                    switched = True
                else:
                    fail_err = _extract_error((sw.stderr or "") + (sw.stdout or ""), sw.returncode, "切换失败")
                    r.err(f"切换失败: {fail_err}")
            else:
                base = _resolve_main_branch(repo)
                r.step(f"分支不存在 → 从 origin/{base} 创建")
                sw = _run(["git", "switch", "-c", target, f"origin/{base}"],
                          cwd=str(repo), check=False, capture_output=True)
                if sw.returncode == 0:
                    r.ok(f"从 origin/{base} 创建并切换到 {target}")
                    switched = True
                else:
                    fail_err = _extract_error((sw.stderr or "") + (sw.stdout or ""), sw.returncode, "创建失败")
                    r.err(f"创建失败: {fail_err}")

        if not switched:
            return "fail", fail_err or "切换/创建失败"

        return "ok", ""

    return _op


def switch_branch_all(target: str) -> int:
    """批量切换分支：扫描 Git 仓库，切换到指定分支（不存在则从主分支创建）。"""
    result = run_batch(
        title=f"分支切换 → {target}",
        root=Path(".").resolve(),
        operation=_switch_one_factory(target),
        confirm=False,
    )
    return 1 if result.failed else 0



# 哨兵: "master" 在 sync_master_all 语境下表示"主分支"语义 (master 或 main),
# 非字面分支名。_sync_one_factory 遇到此值时逐仓探测真实主分支。
_MAIN_SENTINEL = "master"


def _resolve_main_branch(repo: Path) -> str:
    """探测仓库真实主分支。

    优先 origin/HEAD；缺失则尝试 git remote set-head --auto；
    仍失败枚举 origin/main / origin/master 哪个存在；全失败回退 master。
    """
    def _head_ref() -> str | None:
        p = _run(["git", "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"],
                 cwd=str(repo), check=False, capture_output=True)
        if p.returncode == 0:
            out = (p.stdout or "").strip()
            if "/" in out:
                return out.split("/", 1)[1]
            return out or None
        return None

    ref = _head_ref()
    if ref:
        return ref

    # origin/HEAD 未设 → 让 git 探测一次再读
    _run(["git", "remote", "set-head", "origin", "--auto"],
         cwd=str(repo), check=False, capture_output=True)
    ref = _head_ref()
    if ref:
        return ref

    # 兜底: 枚举常见主分支名看远端 ref 是否存在
    for cand in ("main", "master"):
        chk = _run(["git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{cand}"],
                   cwd=str(repo), check=False, capture_output=True)
        if chk.returncode == 0:
            return cand
    return "master"

def _sync_one_factory(branch: str | None, force: bool) -> OperationFn:
    """构造单仓库同步操作。

    branch=None → 同步该仓库当前分支；branch=<name> → 同步指定分支（不还原原 checkout）。
    硬对齐到 origin/<branch>：本地领先默认 skip，--force 才 reset 丢弃。dirty → fail。
    """
    def _op(repo: Path, r: Reporter, _root: Path) -> tuple[str, str]:
        p = _run(["git", "fetch", "--prune", "-q", "origin"],
                 cwd=str(repo), check=False, capture_output=True)
        if p.returncode != 0:
            return "fail", _extract_error((p.stderr or "") + (p.stdout or ""), p.returncode, "fetch 失败")

        if branch is None:
            cur_p = _run(["git", "branch", "--show-current"],
                         cwd=str(repo), check=False, capture_output=True)
            target = (cur_p.stdout or "").strip()
            if not target:
                return "skip", "处于 detached HEAD"
        elif branch == _MAIN_SENTINEL:
            # 主分支语义: 逐仓探测真实 master/main (命令层用 master 即可)
            target = _resolve_main_branch(repo)
        else:
            target = branch

        local = _run(["git", "rev-parse", "--verify", "-q", target],
                     cwd=str(repo), check=False, capture_output=True)
        if local.returncode != 0:
            return "skip", f"无 {target} 分支"

        remote_ref = f"origin/{target}"
        remote = _run(["git", "rev-parse", "--verify", "-q", remote_ref],
                      cwd=str(repo), check=False, capture_output=True)
        if remote.returncode != 0:
            return "skip", f"无 {remote_ref}"

        dirty = _run(["git", "diff-index", "--quiet", "HEAD", "--"],
                     cwd=str(repo), check=False, capture_output=True)
        if dirty.returncode != 0:
            return "fail", _dirty_detail(repo)

        counts_p = _run(
            ["git", "rev-list", "--left-right", "--count", f"{target}...{remote_ref}"],
            cwd=str(repo), check=False, capture_output=True,
        )
        parts = (counts_p.stdout or "0\t0").strip().split()
        ahead = int(parts[0]) if len(parts) >= 1 else 0
        behind = int(parts[1]) if len(parts) >= 2 else 0

        if ahead > 0 and not force:
            log_p = _run(
                ["git", "log", "--oneline", f"{remote_ref}..{target}"],
                cwd=str(repo), check=False, capture_output=True,
            )
            commits = (log_p.stdout or "").strip()
            detail = f"本地 {target} 领先 {ahead} 个 commit"
            if commits:
                detail += "\n" + "\n".join(f"    {line}" for line in commits.splitlines()[:5])
            return "skip", detail

        cur_p = _run(["git", "branch", "--show-current"],
                     cwd=str(repo), check=False, capture_output=True)
        if (cur_p.stdout or "").strip() != target:
            co = _run(["git", "checkout", "-q", target],
                      cwd=str(repo), check=False, capture_output=True)
            if co.returncode != 0:
                return "fail", _extract_error((co.stderr or "") + (co.stdout or ""), co.returncode, f"checkout {target} 失败")

        _run(["git", "reset", "--hard", "-q", remote_ref],
             cwd=str(repo), check=False, capture_output=True)

        sha_p = _run(["git", "rev-parse", "--short", remote_ref],
                     cwd=str(repo), check=False, capture_output=True)
        sha = (sha_p.stdout or "").strip()

        if ahead > 0:
            return "ok", f"强制对齐 {remote_ref} ({sha})，丢弃 {ahead} 本地 commit"
        elif behind > 0:
            return "ok", f"快进 {behind} → {remote_ref} ({sha})"
        else:
            return "ok", f"已在最新 {remote_ref} ({sha})"

    return _op


def sync_branch_all(branch: str | None = None, *, force: bool = False) -> int:
    """批量同步分支：硬对齐到 origin/<branch>（branch=None 同步各仓库当前分支）。"""
    if branch is None:
        title = "同步当前分支 → origin/<当前分支>"
    else:
        title = f"同步 {branch} → origin/{branch}"
    result = run_batch(
        title=title,
        root=Path(".").resolve(),
        operation=_sync_one_factory(branch, force),
        confirm=False,
    )
    return 1 if result.failed else 0


def sync_master_all(*, force: bool = False) -> int:
    """批量同步主分支: 自动识别各仓库 master/main 并硬对齐到 origin/<主分支>。"""
    return sync_branch_all(_MAIN_SENTINEL, force=force)


def _push_branch_one_factory(branch: str | None, force: bool, single: bool = False) -> OperationFn:
    """构造单仓库推送操作（本地 → 远端同名分支）。

    branch=None → 推送该仓库当前分支；branch=<name> → 推送指定分支。
    流程：fetch → pull --ff-only（同步远端到本地）→ push。
    分叉：单仓(single=True)自动 pull --no-rebase 合并（merge commit）；批量仍 skip。
    dirty → fail；--force 用 --force-with-lease。
    """
    def _op(repo: Path, r: Reporter, _root: Path) -> tuple[str, str]:
        p = _run(["git", "fetch", "--prune", "-q", "origin"],
                 cwd=str(repo), check=False, capture_output=True)
        if p.returncode != 0:
            return "fail", _extract_error((p.stderr or "") + (p.stdout or ""), p.returncode, "fetch 失败")

        if branch is None:
            cur_p = _run(["git", "branch", "--show-current"],
                         cwd=str(repo), check=False, capture_output=True)
            target = (cur_p.stdout or "").strip()
            if not target:
                return "skip", "处于 detached HEAD"
        elif branch == _MAIN_SENTINEL:
            # 主分支语义: 逐仓探测真实 master/main (命令层用 master 即可)
            target = _resolve_main_branch(repo)
        else:
            target = branch

        local = _run(["git", "rev-parse", "--verify", "-q", target],
                     cwd=str(repo), check=False, capture_output=True)
        if local.returncode != 0:
            return "skip", f"无 {target} 分支"

        remote_ref = f"origin/{target}"
        remote = _run(["git", "rev-parse", "--verify", "-q", remote_ref],
                      cwd=str(repo), check=False, capture_output=True)
        remote_exists = remote.returncode == 0

        dirty = _run(["git", "diff-index", "--quiet", "HEAD", "--"],
                     cwd=str(repo), check=False, capture_output=True)
        if dirty.returncode != 0:
            return "fail", _dirty_detail(repo)

        # 先同步远端到本地（pull --ff-only）
        if remote_exists:
            r.step(f"pull --ff-only {remote_ref} …")
            pull = _run(["git", "pull", "--ff-only", "-q", "origin", target],
                        cwd=str(repo), check=False, capture_output=True)
            if pull.returncode != 0:
                err = _extract_error(
                    (pull.stderr or "") + (pull.stdout or ""),
                    pull.returncode, "pull --ff-only",
                )
                # 批量场景：skip 不中断；单仓场景：自动 merge 合并分叉
                if not single:
                    return "skip", f"远端有分叉/冲突 — {err}"
                # 单仓：ff-only 失败（分叉）→ 自动 pull --no-rebase 产生 merge commit
                r.step(f"pull --no-rebase（合并分叉）{remote_ref} …")
                pull_merge = _run(
                    ["git", "pull", "--no-rebase", "--no-edit", "origin", target],
                    cwd=str(repo), check=False, capture_output=True,
                )
                if pull_merge.returncode != 0:
                    merr = _extract_error(
                        (pull_merge.stderr or "") + (pull_merge.stdout or ""),
                        pull_merge.returncode, "pull --no-rebase",
                    )
                    return "skip", f"自动 merge 失败（需手动解决冲突）— {merr}"
                if (pull_merge.stdout or "").strip():
                    r.output(pull_merge.stdout)

        # 再推本地到远端
        push_args = ["git", "push"]
        if not remote_exists:
            push_args += ["-u"]
        if force:
            push_args += ["--force-with-lease"]
        push_args += ["origin", target]

        # push 前统计要推送的区间（push 会更新本地 remote-tracking ref，之后无法再数）
        ahead_n = 0
        if remote_exists:
            ahead_n = int((_run(
                ["git", "rev-list", "--count", f"{remote_ref}..HEAD"],
                cwd=str(repo), check=False, capture_output=True,
            ).stdout or "0").strip() or "0")

        r.step(f"push {target} → origin/{target} …")
        push = _run(push_args, cwd=str(repo), check=False, capture_output=True)
        if push.returncode != 0:
            err = _extract_error(
                (push.stderr or "") + (push.stdout or ""),
                push.returncode, "push",
            )
            return "fail", err

        sha_p = _run(["git", "rev-parse", "--short", "HEAD"],
                     cwd=str(repo), check=False, capture_output=True)
        sha = (sha_p.stdout or "").strip()
        if not remote_exists:
            return "ok", f"新建远端分支 origin/{target} ({sha})"
        if ahead_n > 0:
            return "ok", f"推送 {ahead_n} 个 commit → origin/{target} ({sha})"
        return "ok", f"无变化（已在最新 origin/{target}, {sha}）"

    return _op


def push_branch_all(branch: str | None = None, *, force: bool = False) -> int:
    """批量推送分支到远端同名分支（先 pull --ff-only 再 push）。

    branch=None 推送各仓库当前分支。
    单仓时 pull 分叉/冲突暂停等用户解决；多仓批量仍 skip。
    """
    if branch is None:
        title = "推送当前分支 → origin/<当前分支>"
    else:
        title = f"推送 {branch} → origin/{branch}"
    root = Path(".").resolve()
    single = len(scan_repos(root)) == 1
    result = run_batch(
        title=title,
        root=root,
        operation=_push_branch_one_factory(branch, force, single=single),
        confirm=False,
    )
    return 1 if result.failed else 0


def _delete_branch_one_factory(target: str, force: bool) -> OperationFn:
    """构造删除本地分支单仓库操作。

    -D (force) 强删未合并；-d 仅删已合并。
    当前分支 == target → skip；本地无该分支 → skip。
    """
    def _op(repo: Path, r: Reporter, _root: Path) -> tuple[str, str]:
        current = _get_current_branch(cwd=str(repo))
        if current == target:
            return "skip", f"当前分支即 {target}（先 switch 再删）"

        exists = _run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{target}"],
            cwd=str(repo), check=False, capture_output=True,
        )
        if exists.returncode != 0:
            return "skip", f"无本地分支 {target}"

        flag = "-D" if force else "-d"
        p = _run(["git", "branch", flag, target],
                 cwd=str(repo), check=False, capture_output=True)
        if p.returncode != 0:
            err = _extract_error((p.stderr or "") + (p.stdout or ""), p.returncode, "删除失败")
            if not force and "not fully merged" in (p.stderr or ""):
                return "skip", f"{target} 未合并（--force 强删）"
            return "fail", err
        return "ok", f"已删本地 {target}"

    return _op


def delete_branch_all(target: str, *, force: bool = False) -> int:
    """批量删除本地分支。删前确认。"""
    result = run_batch(
        title=f"删除本地分支 {target}" + ("（强删）" if force else ""),
        root=Path(".").resolve(),
        operation=_delete_branch_one_factory(target, force),
        confirm=True,
    )
    return 1 if result.failed else 0


def _delete_branch_remote_one_factory(target: str, remote: str) -> OperationFn:
    """构造删除远端分支单仓库操作。

    git push <remote> --delete <target>。删后 fetch --prune 清 tracking ref。
    无 origin/<target> → skip。
    """
    def _op(repo: Path, r: Reporter, _root: Path) -> tuple[str, str]:
        ref_check = _run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{target}"],
            cwd=str(repo), check=False, capture_output=True,
        )
        if ref_check.returncode != 0:
            return "skip", f"无 {remote}/{target}"

        p = _run(["git", "push", remote, "--delete", target],
                 cwd=str(repo), check=False, capture_output=True)
        if p.returncode != 0:
            err = _extract_error((p.stderr or "") + (p.stdout or ""), p.returncode, "删除失败")
            return "fail", err

        # 清理本地 tracking ref
        _run(["git", "fetch", "--prune", remote],
             cwd=str(repo), check=False, capture_output=True)
        return "ok", f"已删 {remote}/{target}"

    return _op


def delete_branch_remote_all(target: str, *, remote: str = "origin") -> int:
    """批量删除远端分支。删前确认。"""
    result = run_batch(
        title=f"删除远端分支 {remote}/{target}",
        root=Path(".").resolve(),
        operation=_delete_branch_remote_one_factory(target, remote),
        confirm=True,
    )
    return 1 if result.failed else 0

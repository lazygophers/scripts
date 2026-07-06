"""批量 Git 仓库操作公共库。

提供 GitLab 仓库扫描、批量执行、汇总报告等功能。
"""
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from lib.exec import run
from lib.ui import Reporter, reporter


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


def scan_gitlab_repos(root: Path, *, max_depth: int = 3) -> list[Path]:
    """扫描目录下所有 GitLab 仓库（通过 remote URL 匹配）。"""
    repos: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(root, topdown=True):
        # relative_to(root) 在 root 本身为 '.', 子目录为 'a', 'a/b'...
        # 路径组件数 = sep 数 + 1; max_depth 按组件数计 (root 下第 N 层 = N 个组件)
        rel = Path(dirpath).relative_to(root)
        depth = 0 if str(rel) == "." else len(rel.parts)
        if depth >= max_depth:
            dirnames.clear()
            continue
        if ".git" in dirnames:
            repo_dir = Path(dirpath)
            # 检查 remote 是否包含 gitlab
            p = run(["git", "remote", "-v"], cwd=str(repo_dir), check=False, capture_output=True)
            if "gitlab" in (p.stdout or "").lower() or "gitlab" in (p.stderr or "").lower():
                repos.append(repo_dir)
            dirnames.remove(".git")
    repos.sort(key=lambda p: str(p.relative_to(root)))
    return repos


def print_repo_list(r: Reporter, repos: list[Path], root: Path) -> None:
    """打印仓库列表。"""
    for repo in repos:
        r.info(f"  •  {repo.relative_to(root)}")


def print_summary(
    r: Reporter,
    title: str,
    result: BatchResult,
) -> None:
    """打印批量操作汇总。"""
    r.rule(title, style="blue")
    rows = [
        ("仓库总数", str(result.total), None),
        ("✔ 成功", str(len(result.succeeded)), "green" if result.succeeded else None),
        ("⏭ 跳过", str(len(result.skipped)), "dim" if result.skipped else None),
        ("✖ 失败", str(len(result.failed)), "red" if result.failed else None),
    ]
    r.summary("", rows)

    for item in result.succeeded:
        r.ok(f"  {item.name}")
    for item in result.skipped:
        r.info(f"  ⏭ {item.name}" + (f" — {item.detail}" if item.detail else ""))
    for item in result.failed:
        r.err(f"  ✖ {item.name}" + (f" — {item.detail}" if item.detail else ""))


def notify_batch_done(folder_name: str, result: BatchResult, *, script_dir: Path) -> None:
    """批量操作完成通知。"""
    from lib.notify import notify_via_n
    if result.failed:
        msg = f"{folder_name} 部分仓库失败（{len(result.failed)} 个）"
    elif result.succeeded:
        msg = f"{folder_name} 成功同步 {len(result.succeeded)} 个项目"
    else:
        msg = f"{folder_name} 的所有内容完成"
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
    """批量仓库操作公共流程。

    扫描仓库 → 确认 → 逐个执行 → 汇总 → 通知。

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
    repos = scan_gitlab_repos(root)
    r.info(f"共 {len(repos)} 个仓库")
    for repo in repos:
        r.info(f"  •  {repo.relative_to(root)}")

    if confirm and sys.stdin.isatty():
        try:
            answer = input("\n确认执行？(y/N) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in ("y", "yes"):
            r.warn("已取消")
            raise SystemExit(0)

    result = BatchResult(total=len(repos))
    for i, repo in enumerate(repos, 1):
        rel = repo.relative_to(root)
        try:
            r.rule(f"[{i}/{len(repos)}] {rel}")
            status, detail = operation(repo, r, root)
            rr = RepoResult(name=str(rel), path=str(repo), status=status, detail=detail)
            if status == "ok":
                r.ok(f"✔ 同步成功")
                result.succeeded.append(rr)
            elif status == "skip":
                r.info(f"⏭ 跳过" + (f" — {detail}" if detail else ""))
                result.skipped.append(rr)
            else:
                r.err(f"✖ 失败" + (f" — {detail}" if detail else ""))
                result.failed.append(rr)
        except KeyboardInterrupt:
            r.warn("\n用户中断，停止执行")
            break
        except Exception as e:
            r.err(f"✖ 异常: {e}")
            result.failed.append(RepoResult(name=str(rel), path=str(repo), status="fail", detail=str(e)))

    print_summary(r, "执行汇总", result)
    notify_batch_done(folder_name, result, script_dir=script_dir)
    return result


# ── 三个具体批量操作的薄壳入口 ─────────────────────────────────────────
# 用 closure 捕获参数，替代旧实现的模块级 _TARGET/_FORCE/_DRY_RUN/_EXTRA 全局态。

from lib.exec import run as _run
from lib.git import get_current_branch as _get_current_branch


def _pushc_one_factory(dry_run: bool, extra: list[str]) -> OperationFn:
    """构造 pushc 单仓库操作（捕获 dry_run/extra）。"""
    def _op(repo: Path, r: Reporter, _root: Path) -> tuple[str, str]:
        # fetch
        r.step("fetch origin …")
        p = _run(["git", "fetch", "origin"], cwd=str(repo), check=False, capture_output=True)
        if p.returncode != 0:
            r.warn(f"fetch 失败: {(p.stdout or '') + (p.stderr or '')}".strip())

        ref_check = _run(
            ["git", "show-ref", "--verify", "--quiet", "refs/remotes/origin/canary"],
            cwd=str(repo), check=False, capture_output=True,
        )
        remote_canary_exists = ref_check.returncode == 0

        current_branch = _get_current_branch(cwd=str(repo))
        if not current_branch:
            return "skip", "无法获取当前分支（detached HEAD）"
        r.info(f"当前分支: {current_branch}")

        # 条件1：当前分支相对远端 canary 有新 commit
        cond1 = False
        if not remote_canary_exists:
            r.ok("条件1 通过 — 远端 canary 不存在，视为有差异")
            cond1 = True
        else:
            log_p = _run(
                ["git", "log", "origin/canary..HEAD", "--oneline"],
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
                r.info("条件1 不满足 — 当前分支相对 origin/canary 无新 commit")

        # 条件2：本地 canary 相对远端 canary 有差异
        cond2 = False
        local_canary = _run(
            ["git", "show-ref", "--verify", "--quiet", "refs/heads/canary"],
            cwd=str(repo), check=False, capture_output=True,
        )
        if not remote_canary_exists and local_canary.returncode == 0:
            r.ok("条件2 通过 — 远端 canary 不存在但本地 canary 存在")
            cond2 = True
        elif local_canary.returncode == 0:
            diff_p = _run(
                ["git", "log", "origin/canary..canary", "--oneline"],
                cwd=str(repo), check=False, capture_output=True,
            )
            diff_commits = (diff_p.stdout or "").strip()
            if diff_commits:
                count = len(diff_commits.splitlines())
                r.ok(f"条件2 通过 — 本地 canary 领先远端 {count} 个 commit")
                for line in diff_commits.splitlines():
                    r.info(f"       {line}")
                cond2 = True

        if not cond1 and not cond2:
            return "skip", "两个条件均不满足"

        if dry_run:
            return "ok", "条件满足（dry-run 模式，不执行 pushc）"

        r.step("执行 pushc …")
        p = _run(["pushc", *extra], cwd=str(repo), check=False, capture_output=True)
        if p.returncode == 0:
            return "ok", ""
        out = (p.stdout or "") + (p.stderr or "")
        return "fail", out.strip()[:200]

    return _op


def pushc_all(*, dry_run: bool = False, extra: list[str] | None = None) -> int:
    """批量 pushc：扫描 GitLab 仓库，逐个执行 pushc。"""
    run_batch(
        title="pushc 批量推送",
        root=Path(".").resolve(),
        operation=_pushc_one_factory(dry_run, extra or []),
        confirm=False,
    )
    return 0


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

        # 脏工作树
        stashed = False
        diff_p = _run(["git", "diff", "--quiet", "HEAD"], cwd=str(repo), check=False, capture_output=True)
        if diff_p.returncode != 0:
            r.step("工作树有未提交改动 → stash …")
            _run(["git", "stash", "push", "-m", f"switch_branch_auto_{target}"],
                 cwd=str(repo), check=False, capture_output=True)
            stashed = True

        switched = False
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
                r.err("切换失败")
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
                    r.err("切换失败")
            else:
                r.step("分支不存在 → 从 origin/master 创建")
                sw = _run(["git", "switch", "-c", target, "origin/master"],
                          cwd=str(repo), check=False, capture_output=True)
                if sw.returncode == 0:
                    r.ok(f"从 origin/master 创建并切换到 {target}")
                    switched = True
                else:
                    r.err("创建失败")

        if not switched:
            if stashed:
                _run(["git", "stash", "pop"], cwd=str(repo), check=False, capture_output=True)
            return "fail", "切换/创建失败"

        if stashed:
            r.step("恢复 stash …")
            sp = _run(["git", "stash", "pop"], cwd=str(repo), check=False, capture_output=True)
            if sp.returncode == 0:
                r.ok("stash 已恢复")
            else:
                r.warn("stash 恢复有冲突，请手动解决")

        return "ok", ""

    return _op


def switch_branch_all(target: str) -> int:
    """批量切换分支：扫描 GitLab 仓库，切换到指定分支（不存在则从 origin/master 创建）。"""
    run_batch(
        title=f"分支切换 → {target}",
        root=Path(".").resolve(),
        operation=_switch_one_factory(target),
        confirm=False,
    )
    return 0


def _sync_one_factory(force: bool) -> OperationFn:
    """构造 sync_master 单仓库操作（捕获 force）。"""
    def _op(repo: Path, r: Reporter, _root: Path) -> tuple[str, str]:
        p = _run(["git", "fetch", "--prune", "-q", "origin"],
                 cwd=str(repo), check=False, capture_output=True)
        if p.returncode != 0:
            return "fail", "fetch 失败"

        local = _run(["git", "rev-parse", "--verify", "-q", "master"],
                     cwd=str(repo), check=False, capture_output=True)
        if local.returncode != 0:
            return "skip", "无 master 分支"

        remote = _run(["git", "rev-parse", "--verify", "-q", "origin/master"],
                      cwd=str(repo), check=False, capture_output=True)
        if remote.returncode != 0:
            return "skip", "无 origin/master"

        dirty = _run(["git", "diff-index", "--quiet", "HEAD", "--"],
                     cwd=str(repo), check=False, capture_output=True)
        if dirty.returncode != 0:
            return "skip", "工作区有未提交改动"

        counts_p = _run(
            ["git", "rev-list", "--left-right", "--count", "master...origin/master"],
            cwd=str(repo), check=False, capture_output=True,
        )
        parts = (counts_p.stdout or "0\t0").strip().split()
        ahead = int(parts[0]) if len(parts) >= 1 else 0
        behind = int(parts[1]) if len(parts) >= 2 else 0

        if ahead > 0 and not force:
            log_p = _run(
                ["git", "log", "--oneline", "origin/master..master"],
                cwd=str(repo), check=False, capture_output=True,
            )
            commits = (log_p.stdout or "").strip()
            detail = f"本地 master 领先 {ahead} 个 commit"
            if commits:
                detail += "\n" + "\n".join(f"    {line}" for line in commits.splitlines()[:5])
            return "skip", detail

        cur_p = _run(["git", "branch", "--show-current"],
                     cwd=str(repo), check=False, capture_output=True)
        if (cur_p.stdout or "").strip() != "master":
            co = _run(["git", "checkout", "-q", "master"],
                      cwd=str(repo), check=False, capture_output=True)
            if co.returncode != 0:
                return "fail", "checkout master 失败"

        _run(["git", "reset", "--hard", "-q", "origin/master"],
             cwd=str(repo), check=False, capture_output=True)

        sha_p = _run(["git", "rev-parse", "--short", "origin/master"],
                     cwd=str(repo), check=False, capture_output=True)
        sha = (sha_p.stdout or "").strip()

        if ahead > 0:
            return "ok", f"强制对齐 origin/master ({sha})，丢弃 {ahead} 本地 commit"
        elif behind > 0:
            return "ok", f"快进 {behind} → origin/master ({sha})"
        else:
            return "ok", f"已在最新 origin/master ({sha})"

    return _op


def sync_master_all(*, force: bool = False) -> int:
    """批量同步 master：将本地 master 硬对齐到 origin/master。"""
    run_batch(
        title="同步 master → origin/master",
        root=Path(".").resolve(),
        operation=_sync_one_factory(force),
        confirm=False,
    )
    return 0

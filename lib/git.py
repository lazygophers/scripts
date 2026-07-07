"""Git 状态检查与分支管理。"""
import re  # noqa: I001
from pathlib import Path  # noqa: I001
from typing import Optional

from lib.exec import run, retry_command
from lib.ui import Reporter

_DIRTY_RE = re.compile(r"^\?\?|^[ MARCUD]", re.MULTILINE)


class GitError(RuntimeError):
    """Git 操作异常。"""


def check_bit_clean(*, bit_cmd: str = "git") -> None:
    """检查 Git 工作区是否干净（无未提交更改或冲突）。

    Raises:
        GitError: 当仓库状态异常或存在未提交更改时
    """
    p = run([bit_cmd, "status", "--porcelain"], check=False, capture_output=True)
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        raise GitError(out.rstrip() or "无法获取仓库状态，请确认当前目录是 git 仓库。")
    if _DIRTY_RE.search(out):
        raise GitError("存在未提交的更改或未解决的冲突，请先处理！")


def get_current_branch(bit_cmd: str = "git", cwd: str | None = None) -> str:
    """获取当前分支名。"""
    p = run([bit_cmd, "branch", "--show-current"], check=False, capture_output=True, cwd=cwd)
    return (p.stdout or "").strip()


# 向后兼容别名
_get_current_branch = get_current_branch


def _switch_to_branch(branch: str, bit_cmd: str, remote: str, original_branch: str) -> None:
    p = run([bit_cmd, "checkout", branch], check=False, capture_output=True)
    if p.returncode != 0:
        p2 = run(
            [bit_cmd, "checkout", "-b", branch, "--track", f"{remote}/{branch}"],
            check=False,
            capture_output=True,
        )
        if p2.returncode != 0:
            raise GitError(f"切换分支失败，请确认分支 '{branch}' 是否存在！")


def _report(r: Optional[Reporter], method: str, *args, **kwargs) -> None:
    if r is not None and hasattr(r, method):
        try:
            getattr(r, method)(*args, **kwargs)
        except Exception:
            pass


def _rollback_to_branch(original_branch: str, bit_cmd: str) -> None:
    if original_branch:
        run([bit_cmd, "checkout", original_branch], check=False, capture_output=True)


def _run_git_retry(
    cmd: list, *, bit_cmd: str, original_branch: str, r: Optional[Reporter], error_msg: str, title: str
) -> None:
    result = retry_command(cmd, max_retries=3)
    if not result.ok:
        _report(r, "cmd_result", cmd, returncode=1, output=result.last_output, show_output=True, title=title)
        _rollback_to_branch(original_branch, bit_cmd)
        raise GitError(f"{error_msg}: {result.last_output}".rstrip())
    if result.last_output.strip():
        _report(r, "output", result.last_output)


def update_branch(branch: str, *, bit_cmd: str = "git", remote: str = "origin", r: Optional[Reporter] = None) -> None:
    """更新分支：切换到目标分支，同步远程更新，推送本地更改。

    Raises:
        GitError: 当切换分支、拉取或推送失败时
    """
    original_branch = _get_current_branch(bit_cmd)
    retry_ctx = dict(bit_cmd=bit_cmd, original_branch=original_branch, r=r)

    if original_branch != branch:
        _switch_to_branch(branch, bit_cmd, remote, original_branch)

    remote_ref = run([bit_cmd, "ls-remote", "--exit-code", "--heads", remote, branch], check=False, capture_output=True)
    if remote_ref.returncode != 0:
        _report(r, "warn", f"远端不存在 {remote}/{branch}，将先 push -u 创建该分支")
        _run_git_retry(
            [bit_cmd, "push", "-u", remote, branch],
            **retry_ctx, error_msg="推送失败", title="push -u 输出",
        )
        check_bit_clean(bit_cmd=bit_cmd)
        return

    pull_cmd = [bit_cmd, "-c", "merge.autoEdit=false", "pull", remote, branch]
    _report(r, "step", f"{bit_cmd} pull {remote} {branch}")
    _run_git_retry(pull_cmd, **retry_ctx, error_msg="拉取或合并失败", title="pull 输出")
    check_bit_clean(bit_cmd=bit_cmd)

    push_cmd = [bit_cmd, "push", remote, branch]
    _report(r, "step", f"{bit_cmd} push {remote} {branch}")
    _run_git_retry(push_cmd, **retry_ctx, error_msg="推送失败", title="push 输出")


def ensure_tool_exists(cmd: str) -> None:
    from shutil import which
    if which(cmd) is None:
        raise GitError(f"缺少依赖命令: {cmd}")


def remote_branch_exists(branch: str, *, remote: str = "origin", cwd: str | None = None) -> bool:
    """检查远端分支是否存在。"""
    p = run(
        ["git", "ls-remote", "--exit-code", "--heads", remote, branch],
        check=False, capture_output=True, cwd=cwd,
    )
    return p.returncode == 0


def fetch_and_check_branch(
    branch: str,
    *,
    remote: str = "origin",
    cwd: str | None = None,
) -> bool:
    """fetch origin 并检查分支是否存在。返回 True 表示分支存在。"""
    run(["git", "fetch", remote], check=False, capture_output=True, cwd=cwd)
    return remote_branch_exists(branch, remote=remote, cwd=cwd)


# ── fetch_all 薄壳入口 ────────────────────────────

import os  # noqa: E402, I001
from lib.ui import progress, reporter  # noqa: E402, I001


_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _list_top_repos(root: Path) -> list[Path]:
    """列出 root 下顶层（一层）的安全命名 git 仓库。"""
    repos: list[Path] = []
    for name in sorted(os.listdir(root)):
        if name.startswith(".") or not _SAFE_NAME_RE.match(name):
            continue
        p = root / name
        if p.is_dir() and (p / ".git").is_dir():
            repos.append(p)
    return repos


def fetch_all(root: Path = Path(".")) -> int:
    """一键 fetch 所有顶层 Git 仓库的远程更新。"""
    root = root.resolve()
    repos = _list_top_repos(root)
    failures: list[tuple[str, str]] = []

    r = reporter(stderr=True)
    r.rule("Git Fetch All", style="blue")
    r.kv("扫描结果", {"扫描路径": str(root), "仓库数量": str(len(repos))})

    prog = progress(r.console)
    if prog is not None:
        with prog:
            task_id = prog.add_task("fetch", total=len(repos))
            for repo in repos:
                prog.update(task_id, description=f"fetch {repo.name}")
                res = retry_command(["git", "fetch", "--all"], cwd=str(repo), max_retries=3)
                if not res.ok:
                    failures.append((repo.name, res.last_output.strip()))
                    r.err(f"{repo.name}: fetch 失败")
                    if res.last_output.strip():
                        r.output(res.last_output)
                prog.advance(task_id)
    else:
        for repo in repos:
            r.step(f"fetch {repo.name}")
            res = retry_command(["git", "fetch", "--all"], cwd=str(repo), max_retries=3)
            if not res.ok:
                failures.append((repo.name, res.last_output.strip()))
                r.err(f"{repo.name}: 失败")

    r.rule("执行结果", style="green" if not failures else "yellow")
    if failures:
        r.warn(f"失败 {len(failures)}/{len(repos)}")
        for name, _ in failures:
            r.err(name)
        r.ok(f"成功 {len(repos) - len(failures)}/{len(repos)}")
    else:
        r.ok(f"全部成功 ({len(repos)} 个仓库)")

    return 1 if failures else 0

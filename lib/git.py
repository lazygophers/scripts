"""Git 状态检查与分支管理。"""

from __future__ import annotations

import re  # noqa: I001
from pathlib import Path  # noqa: I001

from lib.exec import NET_TIMEOUT, retry_command, run
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


def _report(r: Reporter | None, method: str, *args, **kwargs) -> None:
    if r is not None and hasattr(r, method):
        try:
            getattr(r, method)(*args, **kwargs)
        except Exception:
            pass


def _rollback_to_branch(original_branch: str, bit_cmd: str) -> None:
    if original_branch:
        run([bit_cmd, "checkout", original_branch], check=False, capture_output=True)


def _run_git_retry(
    cmd: list, *, bit_cmd: str, original_branch: str, r: Reporter | None, error_msg: str, title: str
) -> None:
    result = retry_command(cmd, max_retries=3, timeout=NET_TIMEOUT)
    if not result.ok:
        _report(r, "cmd_result", cmd, returncode=1, output=result.last_output, show_output=True, title=title)
        _rollback_to_branch(original_branch, bit_cmd)
        raise GitError(f"{error_msg}: {result.last_output}".rstrip())
    if result.last_output.strip():
        _report(r, "output", result.last_output)


def update_branch(branch: str, *, bit_cmd: str = "git", remote: str = "origin", r: Reporter | None = None, check_after_pull: bool = True) -> None:
    """更新分支：切换到目标分支，同步远程更新，推送本地更改。

    check_after_pull=False 时跳过 pull 后的 check_bit_clean（用于切到目标分支后，
    工作区可能因 gitignore/行尾等残留显示"脏"但无实质改动的场景；合并后的检查
    由调用方在 merge 完成后统一做）。

    Raises:
        GitError: 当切换分支、拉取或推送失败时
    """
    original_branch = _get_current_branch(bit_cmd)
    retry_ctx = dict(bit_cmd=bit_cmd, original_branch=original_branch, r=r)

    if original_branch != branch:
        _switch_to_branch(branch, bit_cmd, remote, original_branch)

    remote_ref = run([bit_cmd, "ls-remote", "--exit-code", "--heads", remote, branch], check=False, capture_output=True, timeout=NET_TIMEOUT)
    if remote_ref.returncode != 0:
        _report(r, "warn", f"远端不存在 {remote}/{branch}，将先 push -u 创建该分支")
        _run_git_retry(
            [bit_cmd, "push", "-u", remote, branch],
            **retry_ctx, error_msg="推送失败", title="push -u 输出",
        )
        if check_after_pull:
            check_bit_clean(bit_cmd=bit_cmd)
        return

    pull_cmd = [bit_cmd, "-c", "merge.autoEdit=false", "pull", remote, branch]
    _report(r, "step", f"{bit_cmd} pull {remote} {branch}")
    _run_git_retry(pull_cmd, **retry_ctx, error_msg="拉取或合并失败", title="pull 输出")
    if check_after_pull:
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
        check=False, capture_output=True, cwd=cwd, timeout=NET_TIMEOUT,
    )
    return p.returncode == 0


def fetch_and_check_branch(
    branch: str,
    *,
    remote: str = "origin",
    cwd: str | None = None,
) -> bool:
    """fetch origin 并检查分支是否存在。返回 True 表示分支存在。"""
    run(["git", "fetch", remote], check=False, capture_output=True, cwd=cwd, timeout=NET_TIMEOUT)
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
        if p.is_dir() and (p / ".git").exists():
            repos.append(p)
    return repos


def fetch_all(root: Path = Path(".")) -> int:
    """一键 fetch 所有顶层 Git 仓库的远程更新。"""
    root = root.resolve()
    repos = _list_top_repos(root)
    failures: list[tuple[str, str]] = []

    r = reporter(stderr=True)
    r.rule("Git Fetch All", style="blue")
    # 单行扫描摘要（禁逐行列仓库，避免与汇总段重复）
    r.info(f"扫描 {len(repos)} 个仓库（{root}）")

    prog = progress(r.console)
    if prog is not None:
        with prog:
            task_id = prog.add_task("fetch", total=len(repos))
            for repo in repos:
                prog.update(task_id, description=f"fetch {repo.name}")
                res = retry_command(["git", "fetch", "--all"], cwd=str(repo), max_retries=3, timeout=NET_TIMEOUT)
                if res.ok:
                    r.status("ok", repo.name)
                else:
                    failures.append((repo.name, res.last_output.strip()))
                    detail = "fetch 失败"
                    if res.last_output.strip():
                        # 失败时附关键错误行（首条非空），便于定位
                        first = next((ln.strip() for ln in res.last_output.splitlines() if ln.strip()), "")
                        if first:
                            detail = f"fetch 失败 — {first[:120]}"
                    r.status("fail", f"{repo.name} {detail}")
                prog.advance(task_id)
    else:
        for repo in repos:
            r.step(f"fetch {repo.name}")
            res = retry_command(["git", "fetch", "--all"], cwd=str(repo), max_retries=3, timeout=NET_TIMEOUT)
            if res.ok:
                r.status("ok", repo.name)
            else:
                failures.append((repo.name, res.last_output.strip()))
                r.status("fail", f"{repo.name} 失败")

    # 汇总：紧凑 Table（status_table 自带标题），无第二个 rule
    failed_names = {name for name, _ in failures}
    items: list[tuple[str, str, str]] = (
        [(repo.name, "ok", "") for repo in repos if repo.name not in failed_names]
        + [(name, "fail", detail.strip()[:120]) for name, detail in failures]
    )
    if failures:
        r.status_table("执行结果", items)
        r.status_footer([(f"失败 {len(failures)}/{len(repos)}", "red")])
    elif repos:
        r.ok(f"全部成功（{len(repos)} 个仓库）")
    else:
        r.info("无仓库可处理")

    return 1 if failures else 0


# ── list_branches 入口 ────────────────────────────

from collections import Counter  # noqa: E402, I001

# for-each-ref 字段分隔符（分支名不含该字符，安全）
_REF_SEP = "\x1f"


def _parse_branch_refs(cwd: str) -> list[dict]:
    """解析单个仓库的本地分支列表。

    用 `git for-each-ref` 一次性取全部分支元信息（分支名 / 是否当前 /
    短 SHA / 最后提交日期 / 上游 / ahead-behind），避免多次 git 调用。
    detached HEAD 时无当前分支，仍列出所有分支。
    """
    fmt = _REF_SEP.join([
        "%(refname:short)",
        "%(HEAD)",
        "%(objectname:short)",
        "%(committerdate:short)",
        "%(upstream:short)",
        "%(upstream:track)",
    ])
    p = run(
        ["git", "for-each-ref", f"--format={fmt}", "refs/heads/"],
        check=False, capture_output=True, cwd=cwd,
    )
    branches: list[dict] = []
    for line in (p.stdout or "").splitlines():
        parts = line.split(_REF_SEP)
        if len(parts) < 6:
            continue
        name, head, sha, date, upstream, track = parts[:6]
        branches.append({
            "name": name,
            "current": head.strip() == "*",
            "sha": sha,
            "date": date,
            "upstream": upstream,
            "track": track.strip(),
        })
    return branches


def _collect_all_branches(repos: list[Path], root: Path) -> list[tuple[str, dict]]:
    """收集所有仓库的分支，返回 (repo_display, branch_dict) 列表。"""
    rows: list[tuple[str, dict]] = []
    for repo in repos:
        display = str(repo.relative_to(root)) if str(repo) != str(root) else repo.name
        for br in _parse_branch_refs(str(repo)):
            rows.append((display, br))
    return rows


def _render_branch_table(
    r,
    rows: list[tuple[str, dict]],
    *,
    mark_duplicates: bool,
) -> None:
    """渲染分支表（Rich Table 或纯文本降级）。"""
    # ponytail: 全局重复标注 — 跨仓库同名分支计数 > 1 标 ⟱
    dup_names: set[str] = set()
    if mark_duplicates:
        counter = Counter(br["name"] for _, br in rows)
        dup_names = {n for n, c in counter.items() if c > 1}

    from lib.ui import Table, Text  # noqa: E402, I001

    if r.console is not None and Table is not None:
        table = Table(
            title="分支总览",
            show_header=True,
            box=None,
            border_style="blue",
            title_style="bold",
            header_style="dim",
            expand=False,
        )
        table.add_column("仓库", style="bold", no_wrap=True)
        table.add_column("分支", no_wrap=True)
        table.add_column("当前", justify="center")
        table.add_column("SHA", style="dim", no_wrap=True)
        table.add_column("日期", no_wrap=True)
        table.add_column("upstream", style="dim", no_wrap=True)
        table.add_column("track", no_wrap=True)
        for repo, br in rows:
            name_text = Text(br["name"])
            if br["current"]:
                name_text.stylize("bold green")
            if br["name"] in dup_names:
                name_text.append("  ⟱", style="yellow bold")
            track = br["track"]
            track_style = "yellow" if "ahead" in track else (
                "red" if "behind" in track else "dim"
            )
            table.add_row(
                repo,
                name_text,
                "●" if br["current"] else "",
                br["sha"],
                br["date"],
                br["upstream"] or "—",
                Text(track or "", style=track_style) if track else "",
            )
        r.console.print(table)
        if dup_names:
            r.warn(f"⟱ = 跨仓库重复分支名（{len(dup_names)} 个）")
        return

    # 纯文本降级
    r.rule("分支总览")
    for repo, br in rows:
        cur = "*" if br["current"] else " "
        dup = " ⟱" if br["name"] in dup_names else ""
        sha = f" {br['sha']} {br['date']}" if br["sha"] else ""
        parts = [f"  {repo}  {cur} {br['name']}{dup}{sha}"]
        if br["upstream"]:
            parts.append(f"[{br['upstream']}")
            if br["track"]:
                parts.append(f" {br['track']}")
            parts.append("]")
        elif br["track"]:
            parts.append(f"[{br['track']}]")
        r._print("", "".join(parts))
    if dup_names:
        r.warn(f"⟱ = 跨仓库重复分支名（{len(dup_names)} 个）")


def list_branches(root: Path = Path(".")) -> int:
    """列出所有仓库的本地分支。

    单仓（root 自身是 git 仓库）→ 仅列该仓库；否则扫描子目录所有 git 仓库。
    Rich 表输出，跨仓库同名分支标 ⟱。
    """
    root = root.resolve()
    r = reporter(stderr=True)

    # 单仓：root/.git 存在 → 仅列该仓
    if (root / ".git").exists():
        repos = [root]
    else:
        from lib.batch_git import scan_repos
        repos = scan_repos(root)

    r.rule("Git 分支总览", style="blue")
    r.info(f"扫描 {len(repos)} 个仓库（{root}）")

    if not repos:
        r.info("无仓库可处理")
        return 0

    rows = _collect_all_branches(repos, root)
    _render_branch_table(r, rows, mark_duplicates=len(repos) > 1)

    total_branches = len(rows)
    r.status_footer([(f"仓库 {len(repos)}", "cyan"), (f"分支 {total_branches}", "green")])
    return 0

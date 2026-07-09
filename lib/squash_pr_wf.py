"""squash_pr 工作流：把 source 自分叉以来的改动压成单 commit → 对接 prc 开 PR。

流程（见 prd FR1-FR10）：
  护栏(flat clean) → fetch → 冲突预演#1 → 建 <source>_pr 分支
  → reset --soft merge-base → 聚合 message → 单 commit → 冲突预演#2
  → push → (可选) lib 源码调 prc_wf.run_prc(target)
任一步失败：回滚（回起始分支 + 删半成品 <source>_pr 本地/远端）+ 语音报错。
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

from lib.exec import retry_command, run, run_logged
from lib.git import GitError, check_bit_clean, get_current_branch, remote_branch_exists
from lib.notify import notify
from lib.ui import Reporter, reporter

REMOTE = "origin"

# git 自动生成的 merge commit subject（--no-merges 已跳 merge commit，
# 这里兜底过滤未合并进线性历史的残留噪声）
_MERGE_NOISE_RE = re.compile(r"^Merge (branch|pull request|tag|remote-tracking)")


class SquashError(RuntimeError):
    """squash_pr 流程异常。"""


def pr_branch_name(source: str) -> str:
    """PR 分支名：<source>_pr。"""
    return f"{source}_pr"


def _strip_remote_prefix(branch: str, remote: str) -> str:
    """剥 <remote>/ 前缀（用户可能传 origin/staging，统一成 staging）。"""
    prefix = f"{remote}/"
    if branch.startswith(prefix):
        return branch[len(prefix):]
    return branch


def fallback_message(source: str, target: str) -> str:
    """聚合为空时的兜底 commit message。"""
    return f"squash: {source} → {target}"


def aggregate_message(subjects: list[str], source: str, target: str) -> str:
    """聚合 commit subjects → 单条 commit message。

    - 过滤 git 自动生成的 merge 噪声（_MERGE_NOISE_RE）
    - 去重保序拼成 subject（用 ` + ` 连接，超长时按需截断）
    - body 列出原 subject bullet
    - 聚合后为空 → 兜底 fallback_message
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for s in subjects:
        s = (s or "").strip()
        if not s or _MERGE_NOISE_RE.match(s):
            continue
        if s in seen:
            continue
        seen.add(s)
        cleaned.append(s)

    if not cleaned:
        return fallback_message(source, target)

    subject = " + ".join(cleaned)
    # subject 单行过长 → 截断（commit subject 软上限 ~72，硬上限 100）
    if len(subject) > 100:
        subject = subject[:97] + "..."
    if len(cleaned) == 1:
        return subject
    body = "\n".join(f"- {s}" for s in cleaned)
    return f"{subject}\n\n{body}"


def _git(args: list[str], *, r: Reporter | None = None, title: str = "",
         show_ok: bool = False, cwd: str | None = None):
    return run_logged(["git", *args], check=False, capture_output=True, r=r,
                      title=title, show_output_on_success=show_ok, cwd=cwd)


def _parse_merge_tree_output(output: str) -> list[str]:
    """从 `git merge-tree --write-tree --name-only` 输出提取冲突文件。

    output 第一行是 tree hash；冲突时后续行是冲突文件路径，
    再之后是空行 + informational messages（Auto-merging / CONFLICT ...）。
    解析策略：跳过首行 tree hash，过滤掉 informational 行（含 "CONFLICT"、
    "Auto-merging"、空行、`<mode> <sha> <stage>` 索引行）。
    """
    lines = output.splitlines()
    files: list[str] = []
    for i, ln in enumerate(lines):
        if i == 0:
            continue  # tree hash
        s = ln.strip()
        if not s:
            continue
        if s.startswith(("Auto-merging", "CONFLICT", "merged-in", "removed in")):
            continue
        # 索引行：<mode> <sha> <stage>\t<path> —— 含 tab 时取 tab 后部分
        if "\t" in s:
            s = s.split("\t", 1)[1]
        # 仍含空格且像 mode/sha 行（6 位 mode + 40 位 sha）→ 跳过
        if re.match(r"^\d{6} [0-9a-f]{40,} \d+ ", ln):
            continue
        files.append(s)
    return files


def detect_conflict(branch_a: str, branch_b: str, *, cwd: str | None = None,
                    r: Reporter | None = None) -> tuple[bool, list[str]]:
    """检测 branch_a 与 branch_b 的三方合并是否会冲突。

    优先用 `git merge-tree --write-tree --name-only`（git ≥ 2.38）：
      exit 0 = 无冲突；exit 1 = 有冲突，stdout 含冲突文件。
    merge-tree 不可用（旧 git / 报错）→ 回退临时 `git merge --no-commit --no-ff`
    在临时 worktree 上跑后 abort（不污染调用方工作区）。

    Returns:
        (has_conflict, conflict_files)
    """
    p = run(["git", "merge-tree", "--write-tree", "--name-only", branch_a, branch_b],
            cwd=cwd, check=False, capture_output=True)
    out = (p.stdout or "")
    # merge-tree --write-tree 仅在 git ≥ 2.38 存在；旧 git 走旧三参数语法会报错。
    # 旧语法：`merge-tree <base> <branch1> <branch2>` 总是 exit 0，不能用于判冲突。
    if p.returncode in (0, 1):
        has = p.returncode == 1
        files = _parse_merge_tree_output(out) if has else []
        return has, files

    # merge-tree 不可用 → 回退临时 merge 探测（在 detached 临时分支上，避免污染）
    if r is not None:
        r.warn(f"merge-tree 不可用（exit={p.returncode}），回退临时 merge 探测")
    return _detect_conflict_via_merge(branch_a, branch_b, cwd=cwd)


def _detect_conflict_via_merge(branch_a: str, branch_b: str, *,
                               cwd: str | None = None) -> tuple[bool, list[str]]:
    """回退方案：临时 merge 探测冲突。在 detached HEAD 上跑，结束 abort + 回原分支。

    注意：会临时切换分支，仅在被 merge-tree 抛弃时使用。
    """
    orig = get_current_branch(cwd=cwd)
    # detached 到 branch_a，再 merge branch_b，看是否冲突
    run(["git", "checkout", "--detach", branch_a], cwd=cwd, check=False, capture_output=True)
    try:
        mp = run(["git", "merge", "--no-commit", "--no-ff", branch_b],
                 cwd=cwd, check=False, capture_output=True)
        if mp.returncode == 0:
            # 无冲突：merge 已成功 staged，回滚到 detached + 重置
            run(["git", "merge", "--abort"], cwd=cwd, check=False, capture_output=True)
            run(["git", "reset", "--hard", "HEAD"], cwd=cwd, check=False, capture_output=True)
            return False, []
        # 冲突：解析 unmerged 文件
        sp = run(["git", "diff", "--name-only", "--diff-filter=U"],
                 cwd=cwd, check=False, capture_output=True)
        files = [ln for ln in (sp.stdout or "").splitlines() if ln.strip()]
        run(["git", "merge", "--abort"], cwd=cwd, check=False, capture_output=True)
        return True, files
    finally:
        if orig:
            run(["git", "checkout", orig], cwd=cwd, check=False, capture_output=True)


@dataclass
class _RollbackState:
    """回滚状态：记录哪些产物需要清理。"""
    original_branch: str = ""
    pr_branch: str = ""
    pr_branch_created_local: bool = False
    pr_branch_pushed: bool = False


@dataclass
class SquashResult:
    returncode: int = 0
    pr_branch: str = ""
    merge_base: str = ""
    message: str = ""
    conflict_files: list[str] = field(default_factory=list)


def _ask_delete_pr_branch(pr_branch: str, *, r: Reporter) -> bool:
    """询问用户是否删除已存在的 <source>_pr 分支。

    非 TTY（CI/管道）→ fail-closed：要求显式 SQUASH_PR_FORCE_DELETE=1。
    返回 True 表示用户同意删除。
    """
    if os.environ.get("SQUASH_PR_FORCE_DELETE") == "1":
        return True
    if not sys.stdin.isatty():
        r.err(f"分支 {pr_branch} 已存在，需交互确认但当前非 TTY。"
              f"设 SQUASH_PR_FORCE_DELETE=1 显式放行删除并重建。")
        return False
    try:
        ans = input(f"\n分支 {pr_branch} 已存在，删除并重建？(y/N) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n"
    return ans in ("y", "yes")


def _rollback(state: _RollbackState, *, r: Reporter, remote: str = REMOTE,
              cwd: str | None = None) -> None:
    """全量回滚：回起始分支 + 删本地/远端 <source>_pr。"""
    if state.pr_branch_pushed:
        run(["git", "push", remote, "--delete", state.pr_branch],
            cwd=cwd, check=False, capture_output=True)
    if state.pr_branch_created_local:
        # 先切回起始分支，否则无法删当前所在分支
        if state.original_branch:
            run(["git", "checkout", state.original_branch],
                cwd=cwd, check=False, capture_output=True)
        run(["git", "branch", "-D", state.pr_branch],
            cwd=cwd, check=False, capture_output=True)


def _fail(msg: str, state: _RollbackState, *, r: Reporter, notify_msg: str = "",
          cwd: str | None = None) -> SquashResult:
    """统一失败路径：回滚 + 语音报错。"""
    r.err(msg)
    _rollback(state, r=r, cwd=cwd)
    try:
        notify(notify_msg or f"squash pr 失败: {msg[:80]}")
    except Exception:
        pass
    return SquashResult(returncode=1)


def run_squash_pr(
    source: str,
    target: str,
    *,
    dry_run: bool = False,
    no_prc: bool = False,
    remote: str = REMOTE,
    r: Reporter | None = None,
    cwd: str | None = None,
) -> SquashResult:
    """执行 squash_pr 主流程。返回 SquashResult（returncode 0 = 成功）。"""
    if r is None:
        r = reporter(stderr=True)
    # 规范化：剥 <remote>/ 前缀（用户可能传 origin/staging 等）
    source = _strip_remote_prefix(source, remote)
    target = _strip_remote_prefix(target, remote)
    pr_branch = pr_branch_name(source)
    state = _RollbackState(pr_branch=pr_branch)

    r.rule("squash_pr", style="blue")
    r.kv("概览", {
        "source": source,
        "target": target,
        "PR 分支": pr_branch,
        "remote": remote,
    })

    # FR1 — 前置护栏
    try:
        check_bit_clean()
    except GitError as e:
        return _fail(f"工作区不干净: {e}", state, r=r, cwd=cwd,
                     notify_msg="工作区不干净，已中止")

    orig = get_current_branch(cwd=cwd)
    if not orig:
        return _fail("无法获取当前分支（detached HEAD？）", state, r=r, cwd=cwd)
    state.original_branch = orig

    # FR2 — fetch + 校验
    # target 必须成功 fetch（冲突预演 / merge-base 需要 origin/<target>）
    r.step(f"fetch {remote} {target}")
    fres = retry_command(["git", "fetch", remote, target],
                         cwd=cwd, max_retries=3)
    if not fres.ok:
        return _fail(f"fetch {target} 失败: {fres.last_output}".rstrip(), state, r=r, cwd=cwd,
                     notify_msg="fetch 失败")
    if fres.last_output.strip():
        r.output(fres.last_output)

    # source 远端可能不存在（新分支未推），单独 fetch 容错：失败仅提示，不阻断
    src_remote_exists = remote_branch_exists(source, remote=remote, cwd=cwd)
    if src_remote_exists:
        r.step(f"fetch {remote} {source}")
        sres = retry_command(["git", "fetch", remote, source],
                             cwd=cwd, max_retries=3)
        if not sres.ok:
            r.warn(f"fetch {source} 失败（忽略，远端比较将跳过）: {sres.last_output}".rstrip())
        elif sres.last_output.strip():
            r.output(sres.last_output)
    else:
        r.info(f"{source} 无远端分支，将仅用本地状态（push 时创建 <source>_pr）")

    # source 落后 origin/<source>？（远端不存在则跳过）
    ahead_behind = run(
        ["git", "rev-list", "--left-right", "--count", f"{source}...{remote}/{source}"],
        cwd=cwd, check=False, capture_output=True,
    )
    if ahead_behind.returncode == 0:
        parts = (ahead_behind.stdout or "").split()
        if len(parts) == 2:
            behind = int(parts[1])
            if behind > 0:
                return _fail(
                    f"{source} 落后 {remote}/{source} {behind} 个 commit，请先 pull/push source",
                    state, r=r, cwd=cwd, notify_msg="source 分支落后远端",
                )

    # 本地存在 target 才更新到 origin/<target> 最新；本地无则跳过
    local_target = run(["git", "rev-parse", "--verify", "--quiet", target],
                       cwd=cwd, check=False, capture_output=True)
    if local_target.returncode == 0:
        r.step(f"更新本地 {target} → {remote}/{target}")
        co_t = _git(["checkout", target], r=r, cwd=cwd)
        if co_t.returncode != 0:
            # checkout 失败时仍停在 orig → 禁止继续 reset --hard，否则会毁掉 orig
            return _fail(f"checkout 本地 {target} 失败: {co_t.stderr}".rstrip(),
                         state, r=r, cwd=cwd, notify_msg="checkout target 失败")
        _git(["reset", "--hard", f"{remote}/{target}"], r=r, cwd=cwd, show_ok=True)
        _git(["checkout", orig], r=r, cwd=cwd)

    # merge-base（reset 基准）
    mb = run(["git", "merge-base", source, f"{remote}/{target}"],
             cwd=cwd, check=False, capture_output=True)
    if mb.returncode != 0:
        return _fail(f"无法计算 merge-base({source}, {remote}/{target})",
                     state, r=r, cwd=cwd, notify_msg="merge-base 计算失败")
    merge_base = (mb.stdout or "").strip()

    # FR3 — 冲突预演 #1：source vs origin/<target>
    r.step(f"冲突预演 #1: {source} vs {remote}/{target}")
    has_conflict, files = detect_conflict(source, f"{remote}/{target}", cwd=cwd, r=r)
    if has_conflict:
        res = SquashResult(returncode=1, conflict_files=files)
        r.err(f"合并冲突（{len(files)} 个文件）:")
        for f in files:
            r.output(f)
        try:
            notify(f"squash pr 检测到合并冲突: {len(files)} 个文件")
        except Exception:
            pass
        return res

    # FR4 — 建 PR 分支
    local_exists = run(["git", "rev-parse", "--verify", "--quiet", pr_branch],
                       cwd=cwd, check=False, capture_output=True).returncode == 0
    remote_exists = remote_branch_exists(pr_branch, remote=remote, cwd=cwd)
    if local_exists or remote_exists:
        if not _ask_delete_pr_branch(pr_branch, r=r):
            return _fail(f"分支 {pr_branch} 已存在，用户选择不删除", state, r=r, cwd=cwd,
                         notify_msg="PR 分支已存在")
        r.step(f"删除已存在的 {pr_branch}")
        if local_exists:
            _git(["branch", "-D", pr_branch], r=r, cwd=cwd)
        if remote_exists:
            _git(["push", remote, "--delete", pr_branch], r=r, cwd=cwd)

    r.step(f"checkout {source} → 创建 {pr_branch}")
    c1 = _git(["checkout", source], r=r, cwd=cwd)
    if c1.returncode != 0:
        return _fail(f"checkout {source} 失败: {c1.stderr}".rstrip(), state, r=r, cwd=cwd,
                     notify_msg="checkout source 失败")
    c2 = _git(["checkout", "-b", pr_branch], r=r, cwd=cwd)
    if c2.returncode != 0:
        # 回到 orig 才能继续后续
        _git(["checkout", orig], r=r, cwd=cwd)
        return _fail(f"创建 {pr_branch} 失败: {c2.stderr}".rstrip(), state, r=r, cwd=cwd,
                     notify_msg="创建 PR 分支失败")
    state.pr_branch_created_local = True

    # FR6 — 聚合 commit message（在 reset 前读 log，reset --soft 不影响 log 历史）
    log_p = run(
        ["git", "log", "--no-merges", "--format=%s", f"{merge_base}..{source}"],
        cwd=cwd, check=False, capture_output=True,
    )
    subjects = [ln for ln in (log_p.stdout or "").splitlines()]
    message = aggregate_message(subjects, source, target)

    # FR5 — squash：reset --soft merge-base
    r.step(f"reset --soft {merge_base}")
    _git(["reset", "--soft", merge_base], r=r, cwd=cwd)

    if dry_run:
        r.rule("演练模式", style="yellow")
        r.kv("计划", {
            "reset 基准": merge_base,
            "commit message": message.splitlines()[0] if message else "",
            "将执行": f"git commit + git push -u {remote} {pr_branch}",
        })
        if not no_prc:
            r.info(f"push 后调 prc {target}")
        # 演练也要回滚已建的分支
        _rollback(state, r=r, cwd=cwd)
        return SquashResult(returncode=0, pr_branch=pr_branch, merge_base=merge_base,
                            message=message)

    # 单 commit
    r.step("commit（单 commit squash）")
    cp = _git(["commit", "-m", message], r=r, cwd=cwd)
    if cp.returncode != 0:
        return _fail(f"commit 失败: {cp.stderr}".rstrip(), state, r=r, cwd=cwd,
                     notify_msg="commit 失败")

    # FR7 — 冲突预演 #2：<source>_pr vs origin/<target>
    r.step(f"冲突预演 #2: {pr_branch} vs {remote}/{target}")
    has_conflict2, files2 = detect_conflict(pr_branch, f"{remote}/{target}", cwd=cwd, r=r)
    if has_conflict2:
        r.err(f"push 前检测到冲突（{len(files2)} 个文件）:")
        for f in files2:
            r.output(f)
        return _fail("push 前冲突预演失败，已回滚", state, r=r, cwd=cwd,
                     notify_msg=f"冲突预演失败: {len(files2)} 个文件")

    # FR8 — push
    r.step(f"push -u {remote} {pr_branch}")
    pp = _git(["push", "-u", remote, pr_branch], r=r, cwd=cwd)
    if pp.returncode != 0:
        return _fail(f"push 失败: {pp.stderr}".rstrip(), state, r=r, cwd=cwd,
                     notify_msg="push 失败")
    state.pr_branch_pushed = True

    r.ok(f"已推送 {pr_branch}（单 commit squash）")

    # FR9 — 对接 prc
    if no_prc:
        r.info("--no-prc: 跳过 prc 调用")
        r.rule("完成", style="green")
        r.summary("squash_pr 完成", [
            ("PR 分支", pr_branch, "green"),
            ("base", f"{remote}/{target}", "cyan"),
            ("停在", pr_branch, "yellow"),
        ])
        return SquashResult(returncode=0, pr_branch=pr_branch,
                            merge_base=merge_base, message=message)

    r.step(f"调 prc {target}")
    rc = _call_prc(target)
    if rc != 0:
        # prc 失败：不回滚已 push 分支（已存在远端，删掉反而损失工作）
        r.err(f"prc 退出码 {rc}（分支 {pr_branch} 已 push，未回滚）")
        r.warn("可手动重跑 prc 或检查 PR 创建结果")
        try:
            notify("squash 已 push，但 prc 创建失败")
        except Exception:
            pass
        return SquashResult(returncode=1, pr_branch=pr_branch,
                            merge_base=merge_base, message=message)

    r.rule("完成", style="green")
    r.summary("squash_pr 完成", [
        ("PR 分支", pr_branch, "green"),
        ("base", f"{remote}/{target}", "cyan"),
        ("PR", "已创建", "green"),
    ])
    return SquashResult(returncode=0, pr_branch=pr_branch,
                        merge_base=merge_base, message=message)


def _call_prc(target: str) -> int:
    """lib 源码调 prc_wf.run_prc(target)。"""
    from lib.prc_wf import run_prc
    try:
        return run_prc(target)
    except Exception:
        return 1

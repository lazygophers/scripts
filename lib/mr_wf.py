"""mr 工作流：检测 provider → 拼 prompt → 调 claude 创建 PR/MR。"""

from __future__ import annotations

import json

from lib.ai_workflow import (
    ProviderInfo,
    current_branch,
    detect_provider,
    detect_self_assignee,
    fmt_opt,
    remote_default_branch,
    run_claude,
)
from lib.exec import run
from lib.ui import reporter


def _find_existing_pr(info: ProviderInfo, *, branch: str, base: str) -> str | None:
    """查 head→base 的 open PR/MR。存在返回 URL，无则 None。

    gh: gh pr list --head <branch> --base <base> --state open --json url
    glab: glab mr list --source-branch <branch> --target-branch <base> --state opened
    查询失败（工具未装/无权限）静默返回 None，不阻断创建。
    """
    if info.provider == "gh":
        p = run(
            ["gh", "pr", "list", "--head", branch, "--base", base,
             "--state", "open", "--json", "url", "--limit", "1"],
            check=False, capture_output=True,
        )
    else:
        p = run(
            ["glab", "mr", "list", "--source-branch", branch,
             "--target-branch", base, "--state", "opened"],
            check=False, capture_output=True,
        )
    if p.returncode != 0:
        return None
    if info.provider == "gh":
        try:
            rows = json.loads(p.stdout or "")
            if rows:
                return rows[0].get("url")
        except (ValueError, TypeError):
            return None
        return None
    # glab: 输出为表格文本，行中含 !<iid> + URL；取首个 http(s) 链接
    for line in (p.stdout or "").splitlines():
        for tok in line.split():
            if tok.startswith("http://") or tok.startswith("https://"):
                return tok
    return None


def run_mr(
    base: str | None = None,
    *,
    dry_run: bool = False,
    draft: bool = True,
    reviews: str | None = None,
    labels: str | None = None,
    assignee: str | None = None,
    settings_file: str | None = None,
) -> int:
    """自动创建 PR/MR。"""
    r = reporter(stderr=True)
    info = detect_provider()
    if info is None:
        r.err("错误: 没有 git remote 或无法解析")
        return 1

    branch = current_branch()

    r.rule("环境", style="blue")
    r.kv("检测", {
        "Provider": info.provider,
        "仓库": info.repo,
        "分支": branch,
    })

    # 默认 assignee 为自己
    if not assignee:
        me = detect_self_assignee(info)
        if me:
            assignee = me
            r.info(f"默认 assignee: {me}")

    # 检测 base
    if not base:
        base = remote_default_branch(info.remote)
    r.info(f"目标分支: {base}")

    if dry_run:
        r.rule("演练", style="yellow")
        r.kv("dry-run", {
            "分支": f"{branch} → {base}",
            "draft": "yes" if draft else "no",
        })
        return 0

    # 查重：head→base 已有 open PR 则跳过，不重复创建
    existing = _find_existing_pr(info, branch=branch, base=base)
    if existing:
        r.ok(f"已存在 open PR，跳过创建：{existing}")
        return 0

    prompt = _build_prompt(
        info, branch=branch, base=base, draft=draft,
        reviews=reviews, labels=labels, assignee=assignee,
    )
    return run_claude(
        prompt,
        system_prompt="你是 PR/MR 创建助手，根据用户输入直接执行 gh/glab 命令创建 PR/MR，不要解释。",
        settings_file=settings_file,
    )


def _build_prompt(
    info: ProviderInfo,
    *,
    branch: str,
    base: str,
    draft: bool,
    reviews: str | None,
    labels: str | None,
    assignee: str | None,
) -> str:
    draft_flag = "--draft" if draft else ""
    rev = fmt_opt("--reviewer", reviews)
    lbl = fmt_opt("--label", labels)
    asn = fmt_opt("--assignee", assignee)
    # 收集非空片段再 join, 避免残留双空格
    extra = " ".join(p for p in (draft_flag, rev, lbl, asn) if p)

    if info.provider == "gh":
        cmd = f'gh pr create --base {base} --title "<title>" --body "<body>" {extra}'.strip()
    else:
        cmd = f'glab mr create --target-branch {base} --title "<title>" --description "<body>" {extra}'.strip()

    # 预注入 commit log + diff stat，claude 不必自己 fetch/log/diff
    run(["git", "fetch", info.remote, base], check=False, capture_output=True)
    log_p = run(["git", "log", f"{info.remote}/{base}..HEAD", "--oneline"],
                check=False, capture_output=True)
    stat_p = run(["git", "diff", "--stat", f"{info.remote}/{base}..HEAD"],
                 check=False, capture_output=True)
    log_block = (log_p.stdout or "").strip() or "（无新 commit）"
    stat_block = (stat_p.stdout or "").strip() or "（无差异）"
    return f"""为分支 '{branch}' 在 '{info.repo}' 创建 {info.provider} PR/MR。上下文已预收集（勿重复跑 git）。

commit log {info.remote}/{base}..HEAD：
{log_block}

diff --stat：
{stat_block}

创建命令（直接执行）：
- {cmd}

规范：
- title：中文，不超 72 字，不加句号（据 commit log 归纳）
- body：## Summary / ## Changes / ## Why / ## Test plan（checkbox）/ ## Notes（可选）
- draft：{'yes' if draft else 'no'}

直接执行命令，不要只输出文本。network error 自动重试一次。"""

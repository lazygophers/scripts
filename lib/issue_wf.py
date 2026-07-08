"""issue 工作流：检测 provider → 拼 prompt → 调 claude 创建 Issue。"""

from __future__ import annotations

from lib.ai_workflow import (
    detect_provider,
    detect_self_assignee,
    fmt_opt,
    run_claude,
)
from lib.ui import reporter


def run_issue(
    title: str | None = None,
    *,
    dry_run: bool = False,
    labels: str | None = None,
    assignee: str | None = None,
    milestone: str | None = None,
    settings_file: str | None = None,
) -> int:
    """自动创建 Issue。"""
    r = reporter(stderr=True)
    info = detect_provider()
    if info is None:
        r.err("错误: 没有 git remote 或无法解析")
        return 1

    r.rule("环境", style="blue")
    r.kv("检测", {
        "Provider": info.provider,
        "仓库": info.repo,
    })

    # 默认 assignee 为自己
    if not assignee:
        me = detect_self_assignee(info)
        if me:
            assignee = me
            r.info(f"默认 assignee: {me}")

    if dry_run:
        r.rule("演练", style="yellow")
        r.kv("dry-run", {"标题": title or "未提供"})
        return 0

    prompt = _build_prompt(
        info, title=title, labels=labels, assignee=assignee, milestone=milestone,
    )
    return run_claude(
        prompt,
        system_prompt="你是 Issue 创建助手，根据用户输入直接执行 gh/glab 命令创建 issue，不要解释。",
        settings_file=settings_file,
    )


def _build_prompt(
    info,
    *,
    title: str | None,
    labels: str | None,
    assignee: str | None,
    milestone: str | None,
) -> str:
    lbl = fmt_opt("--label", labels)
    asn = fmt_opt("--assignee", assignee)
    mil = fmt_opt("--milestone", milestone)
    extra = " ".join(p for p in (lbl, asn, mil) if p)

    if info.provider == "gh":
        cmd = f'gh issue create --title "<title>" --body "<body>" {extra}'.strip()
    else:
        cmd = f'glab issue create --title "<title>" --description "<body>" {extra}'.strip()

    return f"""在 '{info.repo}' 创建 {info.provider} Issue。

Provider 已检测为 '{info.provider}'，host '{info.host}'，repo path '{info.repo}'。

直接执行 {info.provider} 创建命令：
- {cmd}

规范：
- title：中文，不超 80 字，不加句号
- body：## Problem / ## Expected behavior / ## Environment / ## Notes（可选）
- assignee：{assignee or '不指定'}
- labels：{labels or '根据内容推断'}

用户输入：{title or '无，需根据描述推断'}

直接执行命令，不要只输出文本。如遇 network error 自动重试一次。"""

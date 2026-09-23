"""语音通知（macOS say）。"""

from __future__ import annotations

import re

from lib.exec import run, run_logged
from lib.ui import reporter

# CLI flag（--no-say/--debug/--dry-run/--help）已搬 lib/cli_flags.py；
# 这里 re-export 一个版本周期，import 站点逐步迁过去后删除。
from lib.cli_flags import (  # noqa: F401
    consume_debug,
    consume_dry_run,
    consume_help,
    consume_no_say,
    debug_concurrency,
    is_debug,
    is_say_disabled,
    set_debug,
    set_say_disabled,
)

_DANGEROUS_RE = re.compile(r"[;|&$`']")



def project_done_message(suffix: str) -> str:
    """生成项目完成通知消息。"""
    from lib.project import safe_project_context
    return f"{safe_project_context()} {suffix}"


SAY_TIMEOUT_SECS = 30


def notify(msg: str, *, say_cmd: str = "say") -> None:
    """直接调用 say 播报（--no-say / SCRIPTS_NO_SAY=1 时仅打印）。

    带 timeout：macOS TTS 偶发卡死时 say 永不退出，曾把整个测试套件挂死
    （系统边界，必须设超时）。超时只丢语音，不丢消息——文本已打印。
    """
    reporter(stderr=True).info(msg)
    if is_say_disabled():
        return
    from lib.exec import CommandTimeout

    try:
        run([say_cmd, msg], check=False, capture_output=True, timeout=SAY_TIMEOUT_SECS)
    except CommandTimeout:
        reporter(stderr=True).warn(f"语音通知超时（{SAY_TIMEOUT_SECS}s），已跳过")


def notify_via_n(msg: str, *, script_dir=None) -> None:
    """播报通知（直接 say, 忽略 script_dir, 保留参数兼容旧调用）。"""
    notify(msg)


def say_content(content: str) -> int:
    """播报一段文本内容, 含危险字符/长度校验。供 bin/n 薄壳调用。"""
    r = reporter(stderr=True)
    if _DANGEROUS_RE.search(content):
        r.err("n: 输入内容包含潜在的危险字符！")
        return 1
    if len(content) > 500:
        r.err("n: 输入内容过长（最大500个字符）！")
        return 1

    r.step("正在播报通知...")
    p = run_logged(["say", content], check=False, capture_output=True, r=r, title="say", timeout=120)
    if p.returncode == 0:
        r.ok("通知播报成功 ✓")
        return 0
    r.err("通知播报失败！")
    return 1

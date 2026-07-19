"""语音通知（macOS say）。"""

from __future__ import annotations

import os
import re

from lib.exec import run, run_logged
from lib.ui import reporter

_DANGEROUS_RE = re.compile(r"[;|&$`']")

# 全局语音开关：--no-say 或 SCRIPTS_NO_SAY=1 置 True 后，notify 仅打印不发声。
# bin/n 是播报工具本身，不经此开关（用 say_content 直达）。
_SAY_DISABLED = os.environ.get("SCRIPTS_NO_SAY", "") == "1"

# 全局调试开关：--debug 或 SCRIPTS_DEBUG=1 置 True 后，lib/exec 成功命令也输出
# stdout/stderr（默认仅失败时打）。子进程经 env 透传，全链路生效。
_DEBUG = os.environ.get("SCRIPTS_DEBUG", "") == "1"


def set_say_disabled(disabled: bool) -> None:
    """运行时切换语音禁用状态（由 bin 层 --no-say 调用）。"""
    global _SAY_DISABLED
    _SAY_DISABLED = disabled


def is_say_disabled() -> bool:
    return _SAY_DISABLED


def set_debug(debug: bool) -> None:
    """运行时切换调试状态（由 bin 层 --debug 调用）。"""
    global _DEBUG
    _DEBUG = debug


def is_debug() -> bool:
    return _DEBUG


def consume_no_say(argv: list[str]) -> list[str]:
    """剥离 argv 中所有 --no-say 并禁用语音，返回剩余 argv。

    任何 bin 在 argparse 前调用：sys.argv = consume_no_say(sys.argv)。
    剥离而非交给 argparse，避免每个 bin 都注册该参数；对无播报的 bin 也无害。
    局限：对把命令作为 REMAINDER 的 bin（unsleep/loop），若命令本身含 --no-say
    会被一并剥除 — 将 --no-say 置于命令前可避免。
    """
    global _SAY_DISABLED
    if "--no-say" in argv[1:]:
        _SAY_DISABLED = True
        argv = [argv[0]] + [a for a in argv[1:] if a != "--no-say"]
    return argv


def consume_debug(argv: list[str]) -> list[str]:
    """剥离 argv 中所有 --debug 并启用调试输出，返回剩余 argv。

    与 consume_no_say 同构：bin 层 argparse 前调用
    sys.argv = consume_debug(sys.argv)。子进程经 env SCRIPTS_DEBUG=1 透传，
    无需逐个 bin 注册参数。
    """
    global _DEBUG
    if "--debug" in argv[1:]:
        _DEBUG = True
        argv = [argv[0]] + [a for a in argv[1:] if a != "--debug"]
    return argv


def project_done_message(suffix: str) -> str:
    """生成项目完成通知消息。"""
    from lib.project import safe_project_context
    return f"{safe_project_context()} {suffix}"


def notify(msg: str, *, say_cmd: str = "say") -> None:
    """直接调用 say 播报（--no-say / SCRIPTS_NO_SAY=1 时仅打印）。"""
    reporter(stderr=True).info(msg)
    if _SAY_DISABLED:
        return
    run([say_cmd, msg], check=False, capture_output=True)


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
    p = run_logged(["say", content], check=False, capture_output=True, r=r, title="say")
    if p.returncode == 0:
        r.ok("通知播报成功 ✓")
        return 0
    r.err("通知播报失败！")
    return 1

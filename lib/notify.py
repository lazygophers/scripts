"""语音通知（macOS say）。"""
import re

from lib.exec import run, run_logged
from lib.ui import reporter

_DANGEROUS_RE = re.compile(r"[;|&$`']")


def project_done_message(suffix: str) -> str:
    """生成项目完成通知消息。"""
    from lib.project import safe_project_context
    return f"{safe_project_context()} {suffix}"


def notify(msg: str, *, say_cmd: str = "say") -> None:
    """直接调用 say 播报。"""
    reporter(stderr=True).info(msg)
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

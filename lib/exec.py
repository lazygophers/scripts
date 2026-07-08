"""Shell 命令执行与重试。"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional, Sequence


def shell_join(cmd: Sequence[str]) -> str:
    try:
        return shlex.join(list(cmd))
    except Exception:
        return " ".join(cmd)


def run(
    cmd: Sequence[str],
    *,
    cwd: Optional[str] = None,
    check: bool = False,
    capture_output: bool = True,
) -> subprocess.CompletedProcess:
    """执行 shell 命令并返回结果，支持 Ctrl+C 中断。"""
    try:
        return subprocess.run(
            list(cmd),
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            text=True,
            start_new_session=True,
        )
    except KeyboardInterrupt:
        raise KeyboardInterrupt(f"命令被用户中断: {shell_join(cmd)}") from None


def run_no_capture(cmd: Sequence[str], *, cwd: Optional[str] = None) -> int:
    """执行 shell 命令（不捕获输出），支持 Ctrl+C 中断。"""
    try:
        proc = subprocess.Popen(list(cmd), cwd=cwd, start_new_session=True)
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait(timeout=5)
        raise KeyboardInterrupt(f"命令被用户中断: {shell_join(cmd)}") from None


def run_logged(
    cmd: Sequence[str],
    *,
    cwd: Optional[str] = None,
    check: bool = False,
    capture_output: bool = True,
    r: Optional[object] = None,
    title: str = "",
    show_output_on_success: bool = False,
) -> subprocess.CompletedProcess:
    """执行命令并输出日志（需传入 Reporter 实例）。"""
    from lib.ui import Reporter  # 延迟避免循环

    if r is not None:
        _log_before_run(r, cmd, cwd, title)
    p = run(cmd, cwd=cwd, check=False, capture_output=capture_output)
    if r is not None:
        _log_after_run(r, cmd, p, cwd, title, show_output_on_success)
    if check and p.returncode != 0:
        raise subprocess.CalledProcessError(p.returncode, list(cmd), output=p.stdout, stderr=p.stderr)
    return p


def _log_before_run(r, cmd: Sequence[str], cwd: Optional[str], title: str) -> None:
    cmd_s = shell_join(cmd)
    where = f" (cwd={cwd})" if cwd else ""
    head = f"{title}: {cmd_s}{where}" if title else f"{cmd_s}{where}"
    r.step(head)


def _log_after_run(
    r,
    cmd: Sequence[str],
    p: subprocess.CompletedProcess,
    cwd: Optional[str],
    title: str,
    show_output_on_success: bool,
) -> None:
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        r.cmd_result(cmd, cwd=cwd, returncode=p.returncode, output=out, show_output=True, title=title)
    elif show_output_on_success and out.strip():
        r.output(out)


_NETWORK_ERROR_RE = re.compile(
    r"(network|timeout|time\s*out|could not read from remote repository|connection|unable to access)",
    re.IGNORECASE,
)


def looks_like_network_error(output: str) -> bool:
    return bool(_NETWORK_ERROR_RE.search(output))


@dataclass
class RetryResult:
    ok: bool
    attempts: int
    last_output: str


def retry_command(
    cmd: Sequence[str],
    *,
    cwd: Optional[str] = None,
    max_retries: int = 3,
    sleep_seconds: float = 2.0,
) -> RetryResult:
    """执行命令并在网络错误时自动重试。"""
    import time

    attempts = 0
    last_output = ""
    for attempt in range(0, max_retries + 1):
        attempts = attempt + 1
        p = run(cmd, cwd=cwd, check=False, capture_output=True)
        last_output = (p.stdout or "") + (p.stderr or "")
        if p.returncode == 0:
            return RetryResult(ok=True, attempts=attempts, last_output=last_output)
        if not looks_like_network_error(last_output):
            return RetryResult(ok=False, attempts=attempts, last_output=last_output)
        if attempt < max_retries:
            time.sleep(sleep_seconds)
    return RetryResult(ok=False, attempts=attempts, last_output=last_output)

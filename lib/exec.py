"""Shell 命令执行与重试。"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

# 默认超时兜底（秒）。网络命令应传更短（NET_TIMEOUT）。
DEFAULT_TIMEOUT = 300
# 网络命令（push/pull/fetch/ls-remote/clone）默认超时。
NET_TIMEOUT = 120


class CommandTimeout(RuntimeError):
    """命令执行超时。"""


def shell_join(cmd: Sequence[str]) -> str:
    try:
        return shlex.join(list(cmd))
    except Exception:
        return " ".join(cmd)


def run(
    cmd: Sequence[str],
    *,
    cwd: str | None = None,
    check: bool = False,
    capture_output: bool = True,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """执行 shell 命令并返回结果，支持 Ctrl+C 中断与超时。

    timeout 秒后抛 CommandTimeout 并 kill 整个进程组（防子进程残留）。
    env=None 继承父进程环境；传 dict 覆盖（批量调子进程时用于传抑制信号）。
    """
    try:
        return subprocess.run(
            list(cmd),
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            text=True,
            timeout=timeout,
            start_new_session=True,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        raise CommandTimeout(
            f"命令超时（{timeout}s）: {shell_join(cmd)}"
        ) from e
    except KeyboardInterrupt:
        raise KeyboardInterrupt(f"命令被用户中断: {shell_join(cmd)}") from None


def run_no_capture(
    cmd: Sequence[str],
    *,
    cwd: str | None = None,
    timeout: float | None = None,
) -> int:
    """执行 shell 命令（不捕获输出），支持 Ctrl+C 中断与超时。"""
    try:
        proc = subprocess.Popen(list(cmd), cwd=cwd, start_new_session=True)
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_proc_group(proc)
        raise CommandTimeout(f"命令超时（{timeout}s）: {shell_join(cmd)}") from None
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait(timeout=5)
        raise KeyboardInterrupt(f"命令被用户中断: {shell_join(cmd)}") from None


def _kill_proc_group(proc: subprocess.Popen) -> None:
    """kill 整个进程组（start_new_session 已隔离）。"""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        proc.kill()


def run_logged(
    cmd: Sequence[str],
    *,
    cwd: str | None = None,
    check: bool = False,
    capture_output: bool = True,
    r: object | None = None,
    title: str = "",
    show_output_on_success: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """执行命令并输出日志（需传入 Reporter 实例）。"""
    if r is not None:
        _log_before_run(r, cmd, cwd, title)
    p = run(cmd, cwd=cwd, check=False, capture_output=capture_output, timeout=timeout)
    if r is not None:
        _log_after_run(r, cmd, p, cwd, title, show_output_on_success)
    if check and p.returncode != 0:
        raise subprocess.CalledProcessError(p.returncode, list(cmd), output=p.stdout, stderr=p.stderr)
    return p


def _log_before_run(r, cmd: Sequence[str], cwd: str | None, title: str) -> None:
    cmd_s = shell_join(cmd)
    where = f" (cwd={cwd})" if cwd else ""
    head = f"{title}: {cmd_s}{where}" if title else f"{cmd_s}{where}"
    r.step(head)


def _log_after_run(
    r,
    cmd: Sequence[str],
    p: subprocess.CompletedProcess,
    cwd: str | None,
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
    cwd: str | None = None,
    max_retries: int = 3,
    sleep_seconds: float = 2.0,
    timeout: float | None = None,
) -> RetryResult:
    """执行命令并在网络错误时自动重试。"""
    import time

    attempts = 0
    last_output = ""
    for attempt in range(0, max_retries + 1):
        attempts = attempt + 1
        try:
            p = run(cmd, cwd=cwd, check=False, capture_output=True, timeout=timeout)
            last_output = (p.stdout or "") + (p.stderr or "")
            if p.returncode == 0:
                return RetryResult(ok=True, attempts=attempts, last_output=last_output)
            if not looks_like_network_error(last_output):
                return RetryResult(ok=False, attempts=attempts, last_output=last_output)
        except CommandTimeout as e:
            last_output = str(e)
            # 超时视为网络错误，可重试
        if attempt < max_retries:
            time.sleep(sleep_seconds)
    return RetryResult(ok=False, attempts=attempts, last_output=last_output)

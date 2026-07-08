"""进程管理（ps/kill）。"""

from __future__ import annotations

import os
import re
import signal
from typing import Optional, Tuple

from lib.exec import run
from lib.ui import Reporter, Table, reporter

_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _pgrep(pattern: str) -> list[int]:
    """pgrep -f pattern, 返回匹配 pid 列表。"""
    p = run(["pgrep", "-f", pattern], check=False, capture_output=True)
    if p.returncode != 0:
        return []
    out = (p.stdout or "").strip()
    if not out:
        return []
    return [int(line.strip()) for line in out.splitlines() if line.strip().isdigit()]


def kill_by_name(
    patterns: list[str],
    *,
    dry_run: bool = False,
    script_markers: Optional[set[str]] = None,
) -> int:
    """根据进程名批量终止进程。

    - 多 pattern 取交集
    - 排除自身 PID 和 args 含 script_markers 的进程
    - dry_run 仅预览
    返回 0 成功 / 1 有失败 / 非法字符 1。
    """
    r = reporter(stderr=True)
    if script_markers is None:
        script_markers = set()

    for t in patterns:
        if not _NAME_RE.match(t):
            r.err(f"非法字符: '{t}'")
            return 1

    current_pid = os.getpid()

    pid_sets = [set(_pgrep(t)) for t in patterns]
    if not pid_sets:
        unique: list[int] = []
    else:
        intersection = pid_sets[0].copy()
        for s in pid_sets[1:]:
            intersection.intersection_update(s)
        unique = sorted({pid for pid in intersection if pid != current_pid})

    info = ps_info(unique, include_ppid=True)
    filtered: list[int] = []
    for pid in unique:
        row = info.get(pid)
        if not row:
            continue
        args_text = row[3] if len(row) > 3 else ""
        if not args_text or any(m in args_text for m in script_markers):
            continue
        filtered.append(pid)

    if not filtered:
        r.ok(f"未找到进程：{' '.join(patterns)}")
        return 0

    r.rule("进程列表", style="yellow")
    info_filtered = ps_info(filtered, include_ppid=True)
    make_process_table(filtered, info_filtered, "进程列表", r, include_ppid=True)

    if dry_run:
        r.step(f"[dry-run] 将终止 {len(filtered)} 个进程（实际未终止）")
        return 0

    success, fail = kill_pids(filtered, r=r)
    return 1 if fail else 0


def ps_info(pids: list[int], *, include_ppid: bool = False) -> dict[int, Tuple[str, ...]]:
    """查询进程详情。"""
    if not pids:
        return {}
    pid_arg = ",".join(str(p) for p in pids)
    fmt = "pid=,user=,comm=,args=,ppid=" if include_ppid else "pid=,user=,comm=,args="
    p = run(["ps", "-p", pid_arg, "-o", fmt], check=False, capture_output=True)
    out = (p.stdout or "").strip()
    info: dict[int, Tuple[str, ...]] = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 4 if include_ppid else 3)
        expected = 5 if include_ppid else 4
        if len(parts) < expected:
            continue
        pid_s = parts[0]
        try:
            info[int(pid_s)] = tuple(parts)
        except ValueError:
            continue
    return info


def kill_pids(
    pids: list[int],
    *,
    r: Optional[Reporter] = None,
) -> Tuple[int, int]:
    """统一 kill 逻辑，返回 (成功数, 失败数)。"""
    success = 0
    fail = 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
            success += 1
        except Exception:
            fail += 1
    if r is not None:
        if fail == 0:
            r.ok(f"已终止 {success} 个进程")
        else:
            r.warn(f"部分失败：成功 {success}，失败 {fail}")
    return success, fail


def make_process_table(
    pids: list[int],
    info: dict[int, Tuple[str, ...]],
    title: str,
    r: Reporter,
    *,
    include_ppid: bool = False,
) -> None:
    """统一表格输出（支持 Rich 和纯文本降级）。"""
    c = r.console
    if c is not None and Table is not None:
        table = Table(title=title, show_lines=False)
        table.add_column("PID", style="bold yellow", width=8)
        table.add_column("USER", style="cyan", width=10)
        table.add_column("COMMAND", style="green", width=16)
        table.add_column("ARGS", overflow="fold", max_width=45)
        if include_ppid:
            table.add_column("PPID", style="dim", width=8)
        for pid in pids:
            row = info.get(pid)
            if row:
                if include_ppid and len(row) >= 5:
                    pid_s, user, comm, args, ppid = row[0], row[1], row[2], row[3], row[4]
                    table.add_row(pid_s, user, comm, args, ppid)
                else:
                    pid_s, user, comm, args = row[0], row[1], row[2], row[3]
                    table.add_row(pid_s, user, comm, args)
        c.print(table)
    else:
        r.step(f"{title}：")
        for pid in pids:
            row = info.get(pid)
            if row:
                base = f"PID={row[0]} USER={row[1]} COMM={row[2]}"
                if include_ppid and len(row) >= 5:
                    r.info(f"{base} ARGS={row[3]} PPID={row[4]}")
                else:
                    r.info(f"{base} ARGS={row[3]}")


# ── kkp 薄壳入口 ───────────────────────────────────────────────────────

def _validate_port(port: str) -> int:
    if not re.fullmatch(r"[0-9]+", port):
        raise ValueError("端口号必须是数字。")
    p = int(port)
    if p < 1 or p > 65535:
        raise ValueError("端口号必须在 1-65535 范围内。")
    return p


def _lsof_pids(port: int, current_pid: int, script_markers: set[str]) -> list[int]:
    p = run(["lsof", f"-i:{port}", "-n", "-P"], check=False, capture_output=True)
    out = (p.stdout or "").strip()
    if not out:
        return []
    lines = out.splitlines()[1:]
    pids = set()
    for line in lines:
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        if pid == current_pid:
            continue
        # markers 是脚本名（如 "kkp"），lsof 的 COMMAND 列只含进程名不含脚本名，
        # 需查 ps 的完整 command 行才能识别 kkp 自身的 helper 进程。
        if script_markers and _cmdline_matches_markers(pid, script_markers):
            continue
        pids.add(pid)
    return sorted(pids)


def _cmdline_matches_markers(pid: int, markers: set[str]) -> bool:
    """ps 查 pid 的完整命令行，匹配任一 marker 则 True。"""
    p = run(["ps", "-p", str(pid), "-o", "command="], check=False, capture_output=True)
    cmdline = (p.stdout or "").strip()
    if not cmdline:
        return False
    return any(m in cmdline for m in markers)


def kill_by_port(
    port: str,
    *,
    dry_run: bool = False,
    script_markers: set[str] | None = None,
) -> int:
    """根据端口号终止占用进程。供 bin/kkp 薄壳调用。"""
    r = reporter(stderr=True)
    try:
        port_num = _validate_port(port)
    except ValueError as e:
        r.err(f"kkp: {e}")
        return 1

    current_pid = os.getpid()
    markers = script_markers or set()
    pids = _lsof_pids(port_num, current_pid, markers)
    if not pids:
        r.ok(f"端口 {port_num} 空闲")
        return 0

    info = ps_info(pids, include_ppid=True)
    r.rule(f"端口 {port_num} 占用进程", style="yellow")
    make_process_table(pids, info, "进程列表", r, include_ppid=True)

    if dry_run:
        r.step(f"[dry-run] 将终止 {len(pids)} 个进程（实际未终止）")
        return 0

    _success, fail = kill_pids(pids, r=r)
    return 1 if fail else 0

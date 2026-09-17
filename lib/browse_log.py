"""bridge 的服务端日志：一行一条 JSON，写在用户状态目录里。

为什么要落盘：bridge 是个后台进程，出问题的时候（扩展连不上、握手被拒、连接莫名
断开）现场就没了——没有日志时唯一的排查手段是把 `browse bridge run` 拽到前台重跑
一遍，而那时故障往往已经不复现。

**只记事件，不记载荷。** 方法名、成败、断开原因可以写；参数和返回值一律不写，和
扩展侧审计日志同一个口径（`browser-extension/browse/src/redact.ts`）。

轮转：单文件超过 `MAX_BYTES` 就整体挪到 `<名字>.1`（只留一代），再从头写。不按天
切是因为这份日志的读者只有「刚出问题的这一会儿」，留一代够用，也不必起清理任务。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# 单个日志文件的上限。一条记录一百多字节，1 MB 大约存得下几千条，足够回溯一次故障。
MAX_BYTES = 1024 * 1024

# 面板/CLI 一次最多取多少行。取太多没意义，还会把一个 WS 帧撑大。
MAX_TAIL = 500


def log_path() -> Path:
    """日志落点。可用 `BROWSE_BRIDGE_LOG` 覆盖（测试和排查用）。"""
    override = os.environ.get("BROWSE_BRIDGE_LOG")
    if override:
        return Path(override)
    state = os.environ.get("XDG_STATE_HOME")
    root = Path(state) if state else Path.home() / ".local" / "state"
    return root / "lazygophers" / "scripts" / "browse-bridge.log"


def record(event: str, path: Path | None = None, **fields: object) -> None:
    """写一条。写不进去就算了——日志失败绝不能把 bridge 本身带倒。"""
    line = json.dumps({"at": time.time(), "event": event, **fields},
                      ensure_ascii=False, separators=(",", ":"))
    target = log_path() if path is None else path
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _rotate(target)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
    except OSError:
        pass


def _rotate(target: Path) -> None:
    try:
        if target.stat().st_size < MAX_BYTES:
            return
    except FileNotFoundError:
        return
    target.replace(target.with_suffix(f"{target.suffix}.1"))


def tail(limit: int = 100, path: Path | None = None) -> list[dict]:
    """最近 `limit` 条，旧的在前。读不到就是空列表——没有日志不是错误。"""
    target = log_path() if path is None else path
    count = max(1, min(int(limit), MAX_TAIL))
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    out: list[dict] = []
    for line in text.splitlines()[-count:]:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # 写到一半被截断的那一行，跳过
        if isinstance(entry, dict):
            out.append(entry)
    return out


__all__ = ["MAX_BYTES", "MAX_TAIL", "log_path", "record", "tail"]

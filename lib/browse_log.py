"""bridge 的服务端日志：统一走 lib/log.py（单一 JSONL，跨进程安全）。

为什么要落盘：bridge 是个后台进程，出问题的时候（扩展连不上、握手被拒、连接莫名
断开）现场就没了——没有日志时唯一的排查手段是把 `browse bridge run` 拽到前台重跑
一遍，而那时故障往往已经不复现。

**只记事件，不记载荷。** 方法名、成败、断开原因可以写；参数和返回值一律不写，和
扩展侧审计日志同一个口径（`browser-extension/browse/src/redact.ts`）。

本模块只剩薄代理：写经 `lib.log.record`（logger=`browse-daemon`），读经
`lib.log.read_entries`（只取 browse-daemon 的行——统一日志文件里还有别的工具在写）。
"""

from __future__ import annotations

from pathlib import Path

from lib import log as _log

# bridge 写日志用的 logger 名。daemon 进程专属，CLI 侧是 "browse"。
LOGGER = "browse-daemon"

# 面板/CLI 一次最多取多少行。取太多没意义，还会把一个 WS 帧撑大。
MAX_TAIL = 500


def log_path() -> Path:
    """日志落点。可用 `SCRIPTS_LOG`（旧名 `BROWSE_BRIDGE_LOG`）覆盖。"""
    return _log.path()


def record(event: str, path: Path | None = None, **fields: object) -> None:
    """写一条。写不进去就算了——日志失败绝不能把 bridge 本身带倒。"""
    _log.record(event, logger=LOGGER, target=path, **fields)


def tail(limit: int = 100, path: Path | None = None) -> list[dict]:
    """最近 `limit` 条，旧的在前。读不到就是空列表——没有日志不是错误。"""
    count = max(1, min(int(limit), MAX_TAIL))
    return _log.read_entries(path, logger=LOGGER, limit=count)


__all__ = ["LOGGER", "MAX_TAIL", "log_path", "record", "tail"]

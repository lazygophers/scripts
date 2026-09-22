"""统一结构化日志：单一 JSONL 文件，按大小轮转，跨进程安全。

落点：`$TEMP/lazygophers/scripts.log`（`tempfile.gettempdir()` 解析，易失——
适合近期排障，不保证跨重启保留）。每条记录一行 JSON：

    {"at":1760000000.0,"level":"info","logger":"browse","pid":123,"event":"cli.start",…}

轮转：文件到 `DEFAULT_MAX_BYTES`（十进制 10,000,000 bytes）就改名 `.1`，旧的
顺次后移，最多留 `BACKUP_COUNT` 份（`.1`/`.2`/`.3`），最老的删除。

并发：CLI 与 daemon 会同时写这一个文件。标准库 `RotatingFileHandler` 的轮转
在多进程下不安全（两个进程同时撞边界会各转一次、互相覆盖备份），所以
`_LockedRotatingHandler` 把整次写入（含轮转判定）包进 `flock`。锁放在独立的
`<log>.lock` 上——轮转会改名日志文件本身，锁文件不能跟着动。

权限：目录 0700、文件与备份 0600。日志里有路径、域名、错误上下文，不能让
同机其他用户读到。

脱敏：字段名含 password/token/secret/cookie/auth 的值一律写 `<REDACTED>`。
archery / ovpn / email 都在和凭证打交道，纯靠调用方自觉不够。

环境变量（测试与排查缝）：
    SCRIPTS_LOG          覆盖整个文件路径（旧名 BROWSE_BRIDGE_LOG 设了也认）
    SCRIPTS_LOG_LEVEL    DEBUG / INFO / WARNING / ERROR，默认 INFO
    SCRIPTS_LOG_MAXBYTES 覆盖轮转阈值（测试用小值驱动轮转）
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_MAX_BYTES = 10_000_000  # 十进制 10 MB
BACKUP_COUNT = 3
DIR_MODE = 0o700
FILE_MODE = 0o600
REDACTED = "<REDACTED>"
SENSITIVE_KEYS = ("password", "token", "secret", "cookie", "auth")

# LogRecord 自带属性：这些不算事件的附加字段
_STD_ATTRS = set(logging.LogRecord("", logging.INFO, "", 0, "", (), None).__dict__) | {
    "message", "asctime", "taskName",
}

# 当前命令名（Reporter.err 落盘时当 logger 名）。timed()/timed_cli() 进包时设置。
_context: str = ""


def path() -> Path:
    """日志落点。`SCRIPTS_LOG` 覆盖；旧名 `BROWSE_BRIDGE_LOG` 设了也认。"""
    override = os.environ.get("SCRIPTS_LOG") or os.environ.get("BROWSE_BRIDGE_LOG")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "lazygophers" / "scripts.log"


def max_bytes() -> int:
    raw = os.environ.get("SCRIPTS_LOG_MAXBYTES", "")
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_MAX_BYTES


def _level() -> int:
    return getattr(logging, os.environ.get("SCRIPTS_LOG_LEVEL", "INFO").upper(),
                   logging.INFO)


def set_context(name: str) -> None:
    """记下当前命令名，Reporter.err 落盘时用它当 logger。"""
    global _context
    _context = name


def current_name() -> str:
    return _context


class _LockedRotatingHandler(RotatingFileHandler):
    """跨进程安全的轮转写入。

    不走基类的常开 stream：进程 A 轮转改名后，进程 B 手里的旧 fd 还指向改名
    后的文件，继续写进备份里，下一次轮转再互相覆盖。改为每次写入都用 append
    模式按文件名现开现关（打开即定位到文件末尾，不会被旧 inode 骗），整次
    「写 + 超限判断 + 轮转」都在 `<log>.lock` 的 flock 里。`delay=True` 让
    doRollover 结束后不重新常开 stream。
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record) + "\n"
            fd = os.open(f"{self.baseFilename}.lock", os.O_CREAT | os.O_WRONLY, FILE_MODE)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                with open(self.baseFilename, "a", encoding=self.encoding) as handle:
                    os.fchmod(handle.fileno(), FILE_MODE)  # umask 挡不住明文日志
                    handle.write(line)
                if os.path.getsize(self.baseFilename) >= self.maxBytes:
                    self.doRollover()
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
        except Exception:  # noqa: BLE001 — 日志写不进去不能把调用方带倒
            self.handleError(record)


class _JsonlFormatter(logging.Formatter):
    """record → 单行 JSON。附加字段、脱敏、异常三字段都在这里定型。"""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "at": round(record.created, 3),
            "level": record.levelname.lower(),
            "logger": record.name,
            "pid": record.process,
            "event": record.getMessage(),
        }
        extras = {**record.__dict__.get("_reserved_fields", {}),
                  **{k: v for k, v in record.__dict__.items()
                     if k not in _STD_ATTRS and not k.startswith("_")}}
        for key, value in extras.items():
            entry[key] = REDACTED if any(s in key.lower() for s in SENSITIVE_KEYS) else value
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc_type"] = record.exc_info[0].__name__
            entry["exc_value"] = str(record.exc_info[1])
            entry["traceback"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, separators=(",", ":"), default=str)


# (路径, 阈值) → handler。环境变量换了路径，下一次 get_logger 就拿到新键，
# 不会把上一个测试的落点继续写。
_handlers: dict[tuple[str, int], _LockedRotatingHandler] = {}


def _handler(target: Path) -> _LockedRotatingHandler:
    key = (str(target), max_bytes())
    handler = _handlers.get(key)
    if handler is None:
        try:
            try:
                target.parent.mkdir(parents=True, exist_ok=False)
                os.chmod(target.parent, DIR_MODE)  # 只收紧自己新建的目录
            except FileExistsError:
                pass
            handler = _LockedRotatingHandler(
                target, maxBytes=key[1], backupCount=BACKUP_COUNT,
                encoding="utf-8", delay=True)
            handler.setFormatter(_JsonlFormatter())
        except Exception:  # noqa: BLE001 — 落点坏掉（被目录占住等）不能带倒调用方
            return None  # type: ignore[return-value]
        _handlers[key] = handler
    return handler


def get_logger(name: str, *, target: Path | None = None) -> logging.Logger:
    """拿一个写到统一文件的 logger。handler 按 (路径, 阈值) 缓存，换环境变量
    会自动切到新落点——测试里逐用例改 `SCRIPTS_LOG` 不会串。"""
    logger = logging.getLogger(name)
    handler = _handler(path() if target is None else target)
    if handler is not None and logger.handlers != [handler]:
        logger.handlers = [handler]
    logger.setLevel(_level())
    logger.propagate = False
    return logger


def record(event: str, *, logger: str = "", level: str = "info",
           target: Path | None = None, exc_info=None,
           **fields: object) -> None:
    """事件式写入（browse_log.record 同款语义）。

    手拼 LogRecord 而不走 `extra=`：`extra` 的键撞上 LogRecord 保留名（如
    `msg`）会直接 KeyError 丢掉整条记录。写失败不抛——日志绝不能把调用方带倒。
    """
    try:
        name = logger or current_name() or "reporter"
        lg = get_logger(name, target=target)
        lr = logging.LogRecord(lg.name, getattr(logging, level.upper(), logging.INFO),
                               __file__, 0, event, (), None)
        if exc_info is not None and exc_info[0] is not None:
            lr.exc_info = exc_info
        reserved = {k: v for k, v in fields.items() if k in _STD_ATTRS}
        if reserved:
            lr._reserved_fields = reserved  # 撞 LogRecord 保留名的字段（如 msg）
        for key, value in fields.items():
            if key not in _STD_ATTRS:
                setattr(lr, key, value)
        lg.handle(lr)
    except Exception:  # noqa: BLE001
        pass


def install_excepthook(name: str) -> None:
    """未捕获异常先落盘（CRITICAL + 完整堆栈），再交还原 hook。daemon 入口装。

    幂等：daemon 在测试进程里会被反复启动，当前 hook 已是自己装的就不
    再叠一层——链条越叠越长会让一次崩溃写出 N 条 crash。
    """
    current = sys.excepthook
    if getattr(current, "_lg_log_hook", False):
        current._lg_log_name = name  # 已装：只换名字（最近一次启动的 daemon 赢），不叠链
        return
    original = current

    def hook(exc_type, exc_value, exc_tb):
        try:
            record("crash", logger=getattr(hook, "_lg_log_name", "daemon"),
                   level="critical", exc_info=(exc_type, exc_value, exc_tb))
        finally:
            original(exc_type, exc_value, exc_tb)

    hook._lg_log_hook = True
    hook._lg_log_name = name
    sys.excepthook = hook


def read_entries(target: Path | None = None, *, logger: str = "",
                 limit: int = 500) -> list[dict]:
    """读最近 `limit` 条 JSONL（旧的在前）。写到一半的截断行跳过，读不到是空列表。"""
    p = path() if target is None else target
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict] = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        if logger and entry.get("logger") != logger:
            continue
        out.append(entry)
        if len(out) > limit:
            out.pop(0)
    return out

"""browse daemon：Unix socket 服务端 + 请求多路复用。

浏览器只会 fork native host，而用户的 CLI 是另一个进程，两者天生连不上
（`.scratch/browser-control-extension/spec.md` 3.2）。daemon 站在中间：一个 socket，
两类客户端，用握手消息分流。

    CLI ──Command──▶ daemon ──Command(新 id)──▶ native host ──▶ 扩展
    CLI ◀──Success/Error── daemon ◀──Success/Error──┘

传输与路由而已，指令语义全在扩展侧。daemon 只认识 `network.subscribe` /
`network.unsubscribe` 两个方法名，原因见 SUBSCRIBE_METHOD 处的注释。

鉴权就是文件权限（spec 4.2）：父目录 0700、socket 0600，且**创建的那一刻就是**——
先 bind 再 chmod 中间那个窗口足够别的用户连进来。

握手（本层自己的信封，不走协议层的四种，所以不经 decode_frames）：

    客户端 → daemon   {"type": "hello", "role": "cli" | "native-host"}
    daemon → 客户端   {"type": "hello-ack", "connectionId": <int>}

`connectionId` 是这条连接的身份。浏览器每重连一次就换一个，daemon 只认**下发时那条
连接**回的包，上一条连接的迟到回包一律丢弃——多标签页/多连接抢答就是这么挡的
（spec 第 8 节，参照 `AgentDeskAI/browser-tools-mcp:connector.ts:862-905`）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from lib.browse_protocol import (
    ERR_INVALID_ARGUMENT,
    ERR_NOT_CONNECTED,
    MAX_INCOMING_FRAME_BYTES,
    MAX_OUTGOING_FRAME_BYTES,
    ProtocolError,
    command,
    decode_frames,
    error,
)

IDLE_TIMEOUT = 30 * 60.0
# 累积缓冲上限。协议层的 64 MB 是**单帧**上限，拦不住「一直发不完整的帧」——那样
# 缓冲会无限涨。一个最大帧加点余量就是天花板，超了说明对端不可信，直接断开。
MAX_BUFFER_BYTES = MAX_INCOMING_FRAME_BYTES + 1024 * 1024
# 握手帧就两个字段，给 4 KB 已经奢侈；这里卡死是为了让还没验明身份的对端申请不到内存。
MAX_HELLO_BYTES = 4096
READ_CHUNK = 65536

ROLE_CLI = "cli"
ROLE_NATIVE_HOST = "native-host"
ROLES = frozenset({ROLE_CLI, ROLE_NATIVE_HOST})

# 扩展每 20 秒沿端口发一条，用来确认端口没死。daemon 丢掉即可，不是给 CLI 看的。
KEEPALIVE_METHOD = "lg:keepalive.ping"

# daemon 唯一认识的两个方法名。这不是在实现网络语义，是因为「事件该送给哪个 CLI」
# 必须有一个跨扩展重载稳定的订阅标识：扩展的订阅活在 service worker 内存里，浏览器
# 重启或扩展重载后必然清零，连它发的 `net-1` 都会被下一次重新用掉。所以对外的订阅 id
# 由 daemon 发（`sub-N`），扩展给的 id 只在 daemon 内部当转发地址，重连后重建时换掉。
SUBSCRIBE_METHOD = "network.subscribe"
UNSUBSCRIBE_METHOD = "network.unsubscribe"


def socket_path() -> Path:
    """socket 落点：优先 `$XDG_RUNTIME_DIR`，没有就回落用户状态目录。"""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "lazygophers" / "browse.sock"
    return Path.home() / ".local" / "state" / "lazygophers" / "scripts" / "browse.sock"


def pack(message: dict, limit: int = MAX_INCOMING_FRAME_BYTES) -> bytes:
    """信封 → 帧，不做信封校验（握手帧、以及已经解析过的转发包走这条）。

    协议层的 `encode_frame` 一律按 host → 浏览器的 1 MB 卡，daemon → CLI 这个方向
    没有那个限制（截图必然超 1 MB），所以上限在这里按调用方给。
    """
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > limit:
        raise ProtocolError(f"帧体 {len(body)} 字节，超过上限 {limit}")
    return struct.pack("<I", len(body)) + body


async def read_frame(reader: asyncio.StreamReader, limit: int) -> dict:
    """读完整一帧并解 JSON。握手用——那时候还没进 decode_frames 的循环。"""
    header = await reader.readexactly(4)
    (length,) = struct.unpack("<I", header)
    if length == 0 or length > limit:
        raise ProtocolError(f"帧声明 {length} 字节，不在 (0, {limit}] 内")
    body = await reader.readexactly(length)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"帧体不是合法 UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError(f"帧体必须是对象: {payload!r}")
    return payload


def probe(path: Path) -> bool:
    """那头有没有一个活的 daemon。连得上就是有，连不上就是死 socket。"""
    if not path.exists():
        return False
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.connect(str(path))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def bind(path: Path) -> socket.socket:
    """建好父目录并 bind，权限从诞生那一刻就对。

    umask 在 bind 之前设成 0o177，socket 一出生就是 0600，没有「先 0666 再 chmod」
    那个能被别的用户连进来的窗口。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        sock.bind(str(path))
    finally:
        os.umask(old)
    sock.setblocking(False)
    return sock


@dataclass
class _Conn:
    """一条已握手的连接。`id` 就是握手时发回去的 connectionId。"""

    id: int
    role: str
    writer: asyncio.StreamWriter

    async def send(self, message: dict, limit: int = MAX_INCOMING_FRAME_BYTES) -> bool:
        """写一帧。对端已经走了就返回 False，不抛——路由不该被一个死客户端带停。"""
        try:
            self.writer.write(pack(message, limit))
            await self.writer.drain()
        except (ConnectionError, ProtocolError, RuntimeError):
            return False
        return True


@dataclass
class _Pending:
    """一条在途指令。`cli` 为 None 表示 daemon 自己发的（订阅重建）。"""

    cli: _Conn | None
    cli_id: int
    conn_id: int
    method: str
    params: dict = field(default_factory=dict)
    sub_id: str | None = None


@dataclass
class _Sub:
    """一个对外稳定的订阅。`ext_id` 是扩展当前这一世给的地址，重建后会变。"""

    id: str
    cli: _Conn
    params: dict
    ext_id: str = ""


@dataclass
class Daemon:
    """socket 服务端。一个实例一个 socket。"""

    path: Path
    idle_timeout: float = IDLE_TIMEOUT

    _server: asyncio.AbstractServer | None = field(default=None, init=False)
    _browser: _Conn | None = field(default=None, init=False)
    _clis: dict[int, _Conn] = field(default_factory=dict, init=False)
    _pending: dict[int, _Pending] = field(default_factory=dict, init=False)
    _subs: dict[str, _Sub] = field(default_factory=dict, init=False)
    _seq: int = field(default=0, init=False)
    _conn_seq: int = field(default=0, init=False)
    _sub_seq: int = field(default=0, init=False)
    _last_command: float = field(default_factory=time.monotonic, init=False)
    _stopping: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _idle_task: asyncio.Task | None = field(default=None, init=False)

    # ------------------------------------------------------------ 生命周期
    async def start(self) -> bool:
        """起服务。已经有一个在跑就返回 False，什么都不动（幂等）。"""
        if probe(self.path):
            return False
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()  # 连不上的死 socket，删掉重建
        self._server = await asyncio.start_unix_server(self._handle, sock=bind(self.path))
        self._idle_task = asyncio.create_task(self._watch_idle())
        return True

    async def stop(self) -> None:
        """关服务、清 socket。重复调用无害。"""
        self._stopping.set()
        if self._idle_task is not None:
            self._idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_task
            self._idle_task = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    async def wait_stopped(self) -> None:
        """等到自己退出（空闲超时，或别人调了 stop）。"""
        await self._stopping.wait()

    async def _watch_idle(self) -> None:
        """连续 idle_timeout 没指令、且一个客户端都没连着，就自行退出。"""
        tick = min(30.0, max(0.05, self.idle_timeout / 4))
        while True:
            await asyncio.sleep(tick)
            if self._clis or self._browser is not None:
                continue
            if time.monotonic() - self._last_command >= self.idle_timeout:
                self._stopping.set()
                return

    # ------------------------------------------------------------ 连接
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            hello = await read_frame(reader, MAX_HELLO_BYTES)
        except (ProtocolError, asyncio.IncompleteReadError, ConnectionError):
            await _close(writer)
            return
        role = hello.get("role") if hello.get("type") == "hello" else None
        if role not in ROLES:
            with contextlib.suppress(ConnectionError, ProtocolError):
                writer.write(pack({
                    "type": "error",
                    "error": ERR_INVALID_ARGUMENT,
                    "message": f"第一条必须是 {{\"type\":\"hello\",\"role\":\"cli\"|\"native-host\"}}，收到 {hello!r}",
                }, MAX_HELLO_BYTES))
                await writer.drain()
            await _close(writer)
            return

        self._conn_seq += 1
        conn = _Conn(id=self._conn_seq, role=role, writer=writer)
        if not await conn.send({"type": "hello-ack", "connectionId": conn.id}, MAX_HELLO_BYTES):
            await _close(writer)
            return

        await self._attach(conn)
        try:
            await self._pump(conn, reader)
        finally:
            await self._detach(conn)
            await _close(writer)

    async def _attach(self, conn: _Conn) -> None:
        if conn.role == ROLE_CLI:
            self._clis[conn.id] = conn
            return
        old, self._browser = self._browser, conn
        if old is not None:
            await _close(old.writer)  # 一个浏览器就够了，新的顶掉旧的
            await self._fail_pending(old.id, "native host 被新连接顶替")
        await self._rebuild_subscriptions(conn)

    async def _detach(self, conn: _Conn) -> None:
        if conn.role == ROLE_CLI:
            self._clis.pop(conn.id, None)
            await self._drop_subscriptions_of(conn)
            for gid in [g for g, p in self._pending.items() if p.cli is conn]:
                del self._pending[gid]
            return
        if self._browser is conn:
            self._browser = None
        await self._fail_pending(conn.id, "浏览器连接已断开")

    async def _pump(self, conn: _Conn, reader: asyncio.StreamReader) -> None:
        buf = b""
        while True:
            try:
                chunk = await reader.read(READ_CHUNK)
            except ConnectionError:
                return
            if not chunk:
                return
            buf += chunk
            if len(buf) > MAX_BUFFER_BYTES:
                return  # 帧永远发不完 —— 单帧上限管不着这个，缓冲上限管得着
            try:
                messages, buf = decode_frames(buf)
            except ProtocolError:
                return  # 坏帧之后缓冲已经错位，续读只会一路错下去，断开让对端重连
            for message in messages:
                await self._route(conn, message)

    # ------------------------------------------------------------ 路由
    async def _route(self, conn: _Conn, message: dict) -> None:
        if conn.role == ROLE_CLI:
            await self._from_cli(conn, message)
        else:
            await self._from_browser(conn, message)

    async def _from_cli(self, cli: _Conn, message: dict) -> None:
        if "type" in message:
            return  # CLI 只发 Command；没有 type 字段的才是 Command
        self._last_command = time.monotonic()
        if self._browser is None:
            # 不排队不等待：浏览器没开就是没开，等下去只会变成一个无人回收的挂起
            await cli.send(error(message["id"], ERR_NOT_CONNECTED,
                                 "浏览器未连接：确认浏览器开着且扩展已启用"))
            return

        params = message["params"]
        if message["method"] == UNSUBSCRIBE_METHOD:
            params = self._to_ext_subscription(params)

        self._seq += 1
        gid = self._seq
        self._pending[gid] = _Pending(cli=cli, cli_id=message["id"], conn_id=self._browser.id,
                                      method=message["method"], params=message["params"])
        if not await self._browser.send(command(gid, message["method"], params),
                                        limit=MAX_OUTGOING_FRAME_BYTES):
            del self._pending[gid]
            await cli.send(error(message["id"], ERR_NOT_CONNECTED, "指令发不到浏览器：连接已断开"))

    async def _from_browser(self, conn: _Conn, message: dict) -> None:
        kind = message.get("type")
        if kind == "event":
            await self._fanout(message)
            return
        if kind not in ("success", "error"):
            return  # 浏览器不发 Command
        entry = self._pending.get(message["id"])
        if entry is None or entry.conn_id != conn.id:
            return  # 无主回包，或上一条连接的迟到回包 —— 丢掉，不许抢答
        del self._pending[message["id"]]

        if entry.cli is None:
            self._finish_rebuild(entry, message)
            return
        out = dict(message)
        out["id"] = entry.cli_id
        if kind == "success":
            out = self._track_subscription(entry, out)
        await entry.cli.send(out)

    async def _fanout(self, message: dict) -> None:
        if message["method"] == KEEPALIVE_METHOD:
            return
        subs = message["params"].get("subscriptions")
        if not isinstance(subs, list) or not subs:
            for cli in list(self._clis.values()):
                await cli.send(message)
            return
        # 带订阅标记的事件只送给订阅它的那个 CLI，并把扩展的内部 id 换回对外的
        targets: dict[int, tuple[_Conn, list[str]]] = {}
        for ext_id in subs:
            sub = self._sub_by_ext(ext_id)
            if sub is None:
                continue
            targets.setdefault(sub.cli.id, (sub.cli, []))[1].append(sub.id)
        for cli, ids in targets.values():
            await cli.send({**message, "params": {**message["params"], "subscriptions": ids}})

    # ------------------------------------------------------------ 订阅
    def _sub_by_ext(self, ext_id) -> _Sub | None:
        return next((s for s in self._subs.values() if s.ext_id == ext_id), None)

    def _to_ext_subscription(self, params: dict) -> dict:
        """把对外的 `sub-N` 换成扩展当前认的 id。不认识就原样透传，让扩展去报错。"""
        sub = self._subs.get(params.get("subscription"))
        return params if sub is None else {**params, "subscription": sub.ext_id}

    def _track_subscription(self, entry: _Pending, out: dict) -> dict:
        result = out.get("result", {})
        if entry.method == SUBSCRIBE_METHOD and isinstance(result.get("subscription"), str):
            self._sub_seq += 1
            sub = _Sub(id=f"sub-{self._sub_seq}", cli=entry.cli, params=dict(entry.params),
                       ext_id=result["subscription"])
            self._subs[sub.id] = sub
            return {**out, "result": {**result, "subscription": sub.id}}
        if entry.method == UNSUBSCRIBE_METHOD and isinstance(result.get("removed"), list):
            removed = []
            for ext_id in result["removed"]:
                sub = self._sub_by_ext(ext_id)
                removed.append(ext_id if sub is None else self._subs.pop(sub.id).id)
            return {**out, "result": {**result, "removed": removed}}
        return out

    async def _rebuild_subscriptions(self, conn: _Conn) -> None:
        """扩展重连后重下一遍订阅。

        订阅活在 service worker 内存里，浏览器重启或扩展重载后必然没了；CLI 那边还
        举着 `sub-N` 在等事件，所以补齐这件事只能由 daemon 做。
        """
        for sub in list(self._subs.values()):
            self._seq += 1
            gid = self._seq
            self._pending[gid] = _Pending(cli=None, cli_id=0, conn_id=conn.id,
                                          method=SUBSCRIBE_METHOD, params=sub.params, sub_id=sub.id)
            if not await conn.send(command(gid, SUBSCRIBE_METHOD, sub.params), limit=MAX_OUTGOING_FRAME_BYTES):
                del self._pending[gid]
                return

    def _finish_rebuild(self, entry: _Pending, message: dict) -> None:
        sub = self._subs.get(entry.sub_id or "")
        if sub is None:
            return
        ext_id = message.get("result", {}).get("subscription")
        if message.get("type") == "success" and isinstance(ext_id, str):
            sub.ext_id = ext_id
        else:
            del self._subs[sub.id]  # 重建不上就别留着，免得后面按幽灵 id 发 unsubscribe

    async def _drop_subscriptions_of(self, cli: _Conn) -> None:
        gone = [s for s in self._subs.values() if s.cli is cli]
        for sub in gone:
            del self._subs[sub.id]
        if not gone or self._browser is None:
            return
        for sub in gone:  # 尽力回收，扩展那边不然会一直挂着 webRequest 监听
            self._seq += 1
            self._pending[self._seq] = _Pending(cli=None, cli_id=0, conn_id=self._browser.id,
                                                method=UNSUBSCRIBE_METHOD, sub_id=None)
            await self._browser.send(
                command(self._seq, UNSUBSCRIBE_METHOD, {"subscription": sub.ext_id}),
                limit=MAX_OUTGOING_FRAME_BYTES)

    async def _fail_pending(self, conn_id: int, why: str) -> None:
        """浏览器断了，在途指令一条都回不来了，当场失败而不是让 CLI 挂着。"""
        for gid in [g for g, p in self._pending.items() if p.conn_id == conn_id]:
            entry = self._pending.pop(gid)
            if entry.cli is not None:
                await entry.cli.send(error(entry.cli_id, ERR_NOT_CONNECTED, why))


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(ConnectionError, asyncio.CancelledError, OSError):
        await writer.wait_closed()


# ---------------------------------------------------------------- 客户端
async def connect(role: str, path: Path | None = None) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, int]:
    """连上 daemon 并握手，返回 (reader, writer, connectionId)。

    T03 的 native host 用 `role="native-host"`，T04 的 CLI 用 `role="cli"`。
    """
    if role not in ROLES:
        raise ProtocolError(f"role 只能是 {sorted(ROLES)}: {role!r}")
    target = socket_path() if path is None else path
    reader, writer = await asyncio.open_unix_connection(str(target))
    writer.write(pack({"type": "hello", "role": role}, MAX_HELLO_BYTES))
    await writer.drain()
    ack = await read_frame(reader, MAX_HELLO_BYTES)
    if ack.get("type") != "hello-ack":
        await _close(writer)
        raise ProtocolError(f"握手被拒: {ack!r}")
    return reader, writer, ack["connectionId"]


async def run(path: Path | None = None, idle_timeout: float = IDLE_TIMEOUT) -> bool:
    """起 daemon 并守到它退出。已经有一个在跑就直接返回 False。"""
    daemon = Daemon(path=socket_path() if path is None else path, idle_timeout=idle_timeout)
    if not await daemon.start():
        return False
    try:
        await daemon.wait_stopped()
    finally:
        await daemon.stop()
    return True


__all__ = [
    "Daemon",
    "IDLE_TIMEOUT",
    "MAX_BUFFER_BYTES",
    "MAX_HELLO_BYTES",
    "ROLE_CLI",
    "ROLE_NATIVE_HOST",
    "bind",
    "connect",
    "pack",
    "probe",
    "read_frame",
    "run",
    "socket_path",
]

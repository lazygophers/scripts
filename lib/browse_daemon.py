"""browse daemon：Unix socket 服务端 + 请求多路复用。

浏览器只会 fork native host，而用户的 CLI 是另一个进程，两者天生连不上
（`.scratch/browser-control-extension/spec.md` 3.2）。daemon 站在中间：一个 socket，
两类客户端，用握手消息分流。

    CLI ──Command──▶ daemon ──Command(新 id)──▶ native host ──▶ 扩展
    CLI ◀──Success/Error── daemon ◀──Success/Error──┘

传输与路由而已，指令语义全在扩展侧。daemon 只认识 `network.subscribe` /
`network.unsubscribe` 两个方法名，原因见 SUBSCRIBE_METHOD 处的注释。

**安全是转发前的一道闸**（spec 4.4，T11）。每条 CLI 指令先过 `Security.check()`：

    放行            → 照常转发
    命中拒绝名单    → 当场 error，一个包都不发给浏览器
    要确认          → 先 `lg:confirm.request` 问扩展，用户点了才转发

`lg:confirm.request` 是唯一一条 daemon 主动问扩展的路径。它和普通指令共用 `_seq`
id 空间、共用 `_pending`，区别只在 `_Pending.confirm` 上挂了个 future。策略全部在
Python 侧：扩展收到的只有「弹这个框」，收不到 confirm_mode、免确认名单、拒绝名单。
反方向（扩展 → daemon）只有 `lg:approvals.*` 三条，给插件面板读写免确认名单用。

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
    ERR_ABORTED,
    ERR_INVALID_ARGUMENT,
    ERR_NOT_CONNECTED,
    ERR_UNKNOWN_COMMAND,
    ERR_USER_REJECTED,
    MAX_INCOMING_FRAME_BYTES,
    MAX_OUTGOING_FRAME_BYTES,
    ProtocolError,
    command,
    decode_frames,
    error,
    success,
)
from lib.browse_security import (
    Security,
    SecurityError,
    config_view,
    default_config_path,
    domain_of,
    load_config,
    resolve_confirm_mode,
    target_url,
    update_config,
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

# 唯一一条 daemon 主动问扩展的路径（spec 4.4）：策略在 Python 侧算完，扩展只负责把
# 问题摆到用户面前再把布尔值送回来。params 的形状就是 `Security.check()` 的返回值。
CONFIRM_METHOD = "lg:confirm.request"
# 用户不点就永远不回，所以必须有上限。到点按**拒绝**处理 —— 确认这件事只能 fail closed。
CONFIRM_TIMEOUT = 60.0

# 第二条 daemon 主动问扩展的路径（spec 4.3）：`input.*` / `script.*` 的 params 里没有
# url，目标页是扩展按 context / matchUrl / 活动标签页算的，daemon 想拿 deny_domains
# 拦它们就只能问一句。同样不是能力，CLI 上敲不出来。
CONTEXT_URL_METHOD = "lg:context.url"

# `browse stop`（spec 4.5）：中止全部在途指令，daemon 自己留着。daemon 本地执行。
ABORT_METHOD = "lg:daemon.abort"

# 反方向：扩展面板要能列出/增删 per_domain 免确认名单（spec 4.5）。这几条在 daemon
# 本地执行，不转发给任何人。
APPROVALS_LIST = "lg:approvals.list"
APPROVALS_APPROVE = "lg:approvals.approve"
APPROVALS_REVOKE = "lg:approvals.revoke"

# 同方向：扩展的设置页读写 browse.yaml（spec 4.4 / 4.6 的那几个字段）。策略的唯一
# 权威是那个文件 —— 扩展侧不留第二份配置，两份配置一定会漂，而漂的方向是「用户以为
# 关了其实没关」。同样在 daemon 本地执行，CLI 上也敲不出来。
CONFIG_GET = "lg:config.get"
CONFIG_SET = "lg:config.set"


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
    # CLI 握手时带的 `--confirm-mode`。只能收紧不能放宽，判定在 `_gate` 里做。
    confirm_mode: str = ""

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
    """一条在途指令。`cli` 为 None 表示 daemon 自己发的（订阅重建、确认往返）。"""

    cli: _Conn | None
    cli_id: int
    conn_id: int
    method: str
    params: dict = field(default_factory=dict)
    sub_id: str | None = None
    # 有值表示这是一条确认往返，回包（或连接断开）去 set_result 它而不是转发
    confirm: asyncio.Future | None = None
    # 有值表示回包时要记一条审计，`started` 用来算 elapsed_ms
    started: float | None = None


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
    # 安全层（spec 4.4）。默认 None = 第一条指令时按配置文件现建一个；建不出来
    # （配置写错、命令行想放宽）只让那条指令失败，daemon 不倒。
    security: Security | None = None
    override_mode: str = ""
    confirm_timeout: float = CONFIRM_TIMEOUT

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
    # 安全层被丢弃重建时要记得的落点。测试把这两个指到临时目录，重建时丢了它们就会
    # 去读写用户真实的 ~/.config 和审计目录。生产上两个都是 None = 用默认位置。
    _config_path: Path | None = field(default=None, init=False)
    _audit_dir: Path | None = field(default=None, init=False)

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
        mode = hello.get("confirmMode")
        conn = _Conn(id=self._conn_seq, role=role, writer=writer,
                     confirm_mode=mode if isinstance(mode, str) else "")
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
        if message["method"] == ABORT_METHOD:
            await self._abort(cli, message["id"])
            return
        if self._browser is None:
            # 不排队不等待：浏览器没开就是没开，等下去只会变成一个无人回收的挂起
            await cli.send(error(message["id"], ERR_NOT_CONNECTED,
                                 "浏览器未连接：确认浏览器开着且扩展已启用"))
            return

        method, params, cli_id = message["method"], message["params"], message["id"]
        started = time.monotonic()
        if not await self._gate(cli, cli_id, method, params, started):
            return

        forward = self._to_ext_subscription(params) if method == UNSUBSCRIBE_METHOD else params
        self._seq += 1
        gid = self._seq
        self._pending[gid] = _Pending(cli=cli, cli_id=cli_id, conn_id=self._browser.id,
                                      method=method, params=params, started=started)
        if not await self._browser.send(command(gid, method, forward),
                                        limit=MAX_OUTGOING_FRAME_BYTES):
            del self._pending[gid]
            self._audit(method, params, started, result="error", err="指令发不到浏览器")
            await cli.send(error(cli_id, ERR_NOT_CONNECTED, "指令发不到浏览器：连接已断开"))

    async def _abort(self, cli: _Conn, cli_id: int) -> None:
        """`browse stop`：在途指令全部当场失败，daemon 自己留着（spec 4.5）。

        已经发给浏览器的那些副作用可能已经发生了，所以回的是 `failed` 而不是假装
        没做过 —— 和 `run` 的 fail-fast 取消同一个口径。
        """
        why = "已被 `browse stop` 中止，结果未知"
        count = 0
        for gid in list(self._pending):
            entry = self._pending.pop(gid)
            count += 1
            if entry.confirm is not None:
                if not entry.confirm.done():
                    entry.confirm.set_result(None)  # 问到一半被中止，按拒绝算
                continue
            if entry.cli is not None:
                self._audit(entry.method, entry.params, entry.started, result="error", err=why)
                await entry.cli.send(error(entry.cli_id, ERR_ABORTED, why))
        await cli.send(success(cli_id, {"aborted": count}))

    # ------------------------------------------------------------ 安全（spec 4.4）
    def _sec(self) -> Security:
        """安全层单例。建不出来就抛 SecurityError，调用方负责变成 error 回包。"""
        if self.security is None:
            self.security = Security(override_mode=self.override_mode,
                                     config_path=self._config_path,
                                     audit_dir=self._audit_dir)
        return self.security

    def _audit(self, method: str, params: dict, started: float | None, *,
               result: str, err: str | None = None) -> None:
        """记一行审计。安全层没建起来就记不了 —— 那种情况下指令也没发出去。

        ponytail: 同步写盘（append 一行 + 偶尔 prune），在事件循环里直接做。
        真成瓶颈了再挪去 to_thread。
        """
        if self.security is None:
            return
        elapsed = None if started is None else (time.monotonic() - started) * 1000
        with contextlib.suppress(OSError):
            self.security.audit(method, params=params, result=result,
                                elapsed_ms=elapsed, error=err)

    async def _gate(self, cli: _Conn, cli_id: int, method: str, params: dict,
                    started: float) -> bool:
        """转发前的安全检查。放行返回 True；拒绝时已经把 error 回给 CLI 了。"""
        try:
            sec = self._sec()
            # 这条连接自己的 `--confirm-mode`：比配置松就在这里被拒（spec 4.4）
            mode = resolve_confirm_mode(sec.confirm_mode, cli.confirm_mode)
        except SecurityError as exc:
            await cli.send(error(cli_id, exc.code, str(exc)))
            return False

        url = target_url(params)
        if sec.needs_target_lookup(method, params, mode):
            url = await self._ask_target_url(params)
            if url is None and sec.deny_domains:
                # 拒绝名单非空却问不出目标页 —— 不知道就不放行（fail closed）。
                # 名单空着时问不到不算错，照旧带着 url=None 往下走。
                why = (f"配了 deny_domains，但问不到 {method} 的目标页面 URL"
                       f"（浏览器没答上来），按拒绝处理")
                self._audit(method, params, started, result="denied", err=why)
                await cli.send(error(cli_id, ERR_USER_REJECTED, why))
                return False

        try:
            req = sec.check(method, params, url=url, mode=mode)
        except SecurityError as exc:
            # 拒绝名单优先级高于确认：命中就直接拒，一个确认框都不弹
            self._audit(method, params, started, result="denied", err=str(exc))
            await cli.send(error(cli_id, exc.code, str(exc)))
            return False
        if req is None:
            return True

        reply = await self._ask(CONFIRM_METHOD, req)
        if not (reply is not None and reply.get("approved") is True):
            why = f"用户拒绝了 {req['action']}（{method}）"
            self._audit(method, params, started, result="denied", err=why)
            await cli.send(error(cli_id, ERR_USER_REJECTED, why))
            return False
        if mode == "per_domain":
            domain = domain_of(req["url"])
            if domain:
                sec.approve(domain)  # 同意即落盘，同域名下次不再问
        return True

    async def _ask_target_url(self, params: dict) -> str | None:
        """问扩展：这条指令会落在哪个页面上（`context` > `matchUrl` > 活动标签页）。

        目标选择规则只有扩展知道（`handlers/context.ts:41`），在 daemon 里照抄一遍
        必然漂移，所以直接问它。问不到返回 None，调用方 fail closed。
        """
        ask = {key: params[key] for key in ("context", "matchUrl") if key in params}
        reply = await self._ask(CONTEXT_URL_METHOD, ask)
        url = None if reply is None else reply.get("url")
        return url if isinstance(url, str) and url else None

    async def _ask(self, method: str, params: dict) -> dict | None:
        """daemon 主动问扩展一句，等它的 result。

        超时、送不出去、连接中途断掉一律返回 None —— 这两条路径（确认、目标页 URL）
        的调用方都把 None 当拒绝，所以问不到就是 fail closed。
        """
        browser = self._browser
        if browser is None:
            return None
        self._seq += 1
        gid = self._seq
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[gid] = _Pending(cli=None, cli_id=0, conn_id=browser.id,
                                      method=method, confirm=fut)
        try:
            if not await browser.send(command(gid, method, params),
                                      limit=MAX_OUTGOING_FRAME_BYTES):
                return None
            return await asyncio.wait_for(asyncio.shield(fut), self.confirm_timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return None
        finally:
            self._pending.pop(gid, None)

    async def _from_browser(self, conn: _Conn, message: dict) -> None:
        kind = message.get("type")
        if kind == "event":
            await self._fanout(message)
            return
        if kind is None:
            # 扩展面板 / 设置页问 daemon 要配置（spec 4.5）。本地执行，不转发。
            if message.get("method") in (CONFIG_GET, CONFIG_SET):
                await self._config(conn, message)
            else:
                await self._approvals(conn, message)
            return
        if kind not in ("success", "error"):
            return
        entry = self._pending.get(message["id"])
        if entry is None or entry.conn_id != conn.id:
            return  # 无主回包，或上一条连接的迟到回包 —— 丢掉，不许抢答
        del self._pending[message["id"]]

        if entry.confirm is not None:
            if not entry.confirm.done():
                result = message.get("result") if kind == "success" else None
                entry.confirm.set_result(result if isinstance(result, dict) else None)
            return
        if entry.cli is None:
            self._finish_rebuild(entry, message)
            return
        out = dict(message)
        out["id"] = entry.cli_id
        if kind == "success":
            out = self._track_subscription(entry, out)
        self._audit(entry.method, entry.params, entry.started,
                    result="success" if kind == "success" else "error",
                    err=None if kind == "success" else message.get("message"))
        await entry.cli.send(out)

    async def _approvals(self, conn: _Conn, message: dict) -> None:
        """`lg:approvals.list / approve / revoke`，扩展面板的免确认名单读写。"""
        method, cid = message["method"], message["id"]
        if method not in (APPROVALS_LIST, APPROVALS_APPROVE, APPROVALS_REVOKE):
            await conn.send(error(cid, ERR_UNKNOWN_COMMAND, f"daemon 不处理 {method}"))
            return
        try:
            sec = self._sec()
            if method != APPROVALS_LIST:
                domain = domain_of(message["params"].get("domain"))
                if not domain:
                    raise SecurityError("要给一个 domain", code=ERR_INVALID_ARGUMENT)
                (sec.approve if method == APPROVALS_APPROVE else sec.revoke)(domain)
        except SecurityError as exc:
            await conn.send(error(cid, exc.code, str(exc)))
            return
        await conn.send(success(cid, {"domains": sec.approvals()}))

    async def _config(self, conn: _Conn, message: dict) -> None:
        """`lg:config.get / set`，扩展设置页读写 browse.yaml。

        写完把安全层丢掉重建：不丢的话设置页改了模式，已经在跑的 daemon 还按旧策略
        放行 —— 用户以为关了其实没关，正是这条链路最不能出的错。
        """
        method, cid = message["method"], message["id"]
        if self.security is not None:
            self._config_path = self.security.config_path
            self._audit_dir = self.security.audit_dir
        path = self._config_path
        try:
            if method == CONFIG_SET:
                update_config(message.get("params") or {}, path)
                # 丢掉安全层，下一条指令按新配置重建。不丢的话设置页改了模式，daemon
                # 还按旧策略放行 ——「用户以为关了其实没关」正是这条链路最不能出的错。
                # 只丢不立刻重建：新配置和启动时的 `--confirm-mode` 撞上时，该让那条
                # 指令失败，而不是让这次保存报错（配置其实已经写进去了）。
                self.security = None
            cfg = load_config(path)
        except SecurityError as exc:
            await conn.send(error(cid, exc.code, str(exc)))
            return
        except OSError as exc:
            await conn.send(error(cid, ERR_INVALID_ARGUMENT, f"配置文件读写失败：{exc}"))
            return
        await conn.send(success(cid, {"config": config_view(cfg),
                                      "path": str(path or default_config_path())}))

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
            if entry.confirm is not None:
                if not entry.confirm.done():
                    entry.confirm.set_result(None)  # 没人能回答了，按拒绝算
                continue
            if entry.cli is not None:
                self._audit(entry.method, entry.params, entry.started, result="error", err=why)
                await entry.cli.send(error(entry.cli_id, ERR_NOT_CONNECTED, why))


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(ConnectionError, asyncio.CancelledError, OSError):
        await writer.wait_closed()


# ---------------------------------------------------------------- 客户端
async def connect(role: str, path: Path | None = None, *,
                  confirm_mode: str = "") -> tuple[asyncio.StreamReader, asyncio.StreamWriter, int]:
    """连上 daemon 并握手，返回 (reader, writer, connectionId)。

    T03 的 native host 用 `role="native-host"`，T04 的 CLI 用 `role="cli"`。

    `confirm_mode` 是这次调用的 `--confirm-mode`：随握手带过去，对这条连接上的每条
    指令生效。daemon 只接受收紧，放宽会被它拒掉（spec 4.4）。
    """
    if role not in ROLES:
        raise ProtocolError(f"role 只能是 {sorted(ROLES)}: {role!r}")
    target = socket_path() if path is None else path
    reader, writer = await asyncio.open_unix_connection(str(target))
    hello = {"type": "hello", "role": role}
    if confirm_mode:
        hello["confirmMode"] = confirm_mode
    writer.write(pack(hello, MAX_HELLO_BYTES))
    await writer.drain()
    ack = await read_frame(reader, MAX_HELLO_BYTES)
    if ack.get("type") != "hello-ack":
        await _close(writer)
        raise ProtocolError(f"握手被拒: {ack!r}")
    return reader, writer, ack["connectionId"]


async def run(path: Path | None = None, idle_timeout: float = IDLE_TIMEOUT,
              override_mode: str = "") -> bool:
    """起 daemon 并守到它退出。已经有一个在跑就直接返回 False。"""
    daemon = Daemon(path=socket_path() if path is None else path, idle_timeout=idle_timeout,
                    override_mode=override_mode)
    if not await daemon.start():
        return False
    try:
        await daemon.wait_stopped()
    finally:
        await daemon.stop()
    return True


__all__ = [
    "ABORT_METHOD",
    "APPROVALS_APPROVE",
    "APPROVALS_LIST",
    "APPROVALS_REVOKE",
    "CONFIG_GET",
    "CONFIG_SET",
    "CONFIRM_METHOD",
    "CONFIRM_TIMEOUT",
    "CONTEXT_URL_METHOD",
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

"""browse daemon：Unix socket 服务端 + 请求多路复用。

浏览器只会 fork native host，而用户的 CLI 是另一个进程，两者天生连不上
（`.scratch/browser-control-extension/spec.md` 3.2）。daemon 站在中间：一个 socket，
两类客户端，用握手消息分流。

    CLI ──Command──▶ daemon ──Command(新 id)──▶ native host ──▶ 扩展
    CLI ◀──Success/Error── daemon ◀──Success/Error──┘

**一台机器上可以同时连着好几个浏览器。** `browse install` 默认给探测到的每个浏览器都
注册通信配置，所以 Chrome 和 Brave 同时开着是常态。它们各自一条 native host 连接，按
**浏览器名**分槽（`_browsers`）—— 名字是安装时写死在各自 wrapper 里的 `--browser`
参数，不靠扩展去猜（Brave 的 UA 伪装成 Chrome，Edge 只差一个 `Edg/`，猜不得）。

挑哪个浏览器的规则照搬 `lib/profile_store.ProfileStore.resolve()`：显式指定优先，只有
一个连着时不用指定，多个连着又没指定就报错并把都有谁列出来。

传输与路由而已，指令语义全在扩展侧。daemon 只认识 `network.subscribe` /
`network.unsubscribe` 两个方法名，原因见 SUBSCRIBE_METHOD 处的注释。

**daemon 不做任何裁决**（2026-09-14 起）。确认模式、拒绝名单、免确认名单、审计全部
在插件里，存 `chrome.storage.local`（`browser-extension/browse/src/policy.ts`）。
这里只剩转发：CLI 发什么就往浏览器递什么，浏览器回什么就往 CLI 递什么。

为什么这样是对的：daemon 只有在转发指令时才需要裁决，而它能转发的前提就是插件连着。
插件不在就没有指令可拦 —— 所以「把闸门放在插件里」不留任何缺口，反而省掉了一整条
`lg:confirm.request` 的往返和一份会和插件漂移的配置。

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
import hashlib
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
    MAX_INCOMING_FRAME_BYTES,
    MAX_OUTGOING_FRAME_BYTES,
    ProtocolError,
    command,
    decode_frames,
    error,
    success,
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

# `browse stop`（spec 4.5）：中止全部在途指令，daemon 自己留着。daemon 本地执行。
ABORT_METHOD = "lg:daemon.abort"

# `browse daemon status`：现在连着哪些浏览器。也是 daemon 本地执行，不转发。
BROWSERS_METHOD = "lg:daemon.browsers"


class _NoBrowser(Exception):
    """挑不出浏览器。`code` 直接就是回给 CLI 的错误码。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# macOS sun_path 上限 104 字节（Linux 108）。超长路径 bind 直接
# OSError: AF_UNIX path too long —— 2026-09-22/23 实测刷了 983 次崩溃循环。
_UNIX_PATH_MAX = 104


def _fit_unix_path(path: Path) -> Path:
    """超长路径截断尾部、用内容哈希兜底，保证 ≤ _UNIX_PATH_MAX 字节。"""
    raw = str(path)
    if len(raw.encode()) <= _UNIX_PATH_MAX:
        return path
    digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
    cut = _UNIX_PATH_MAX - len(digest) - 1 - len(path.suffix)
    return Path(raw[:cut] + "." + digest + path.suffix)


def socket_path() -> Path:
    """socket 落点：优先 `$XDG_RUNTIME_DIR`，没有就回落用户状态目录。
    超过 AF_UNIX sun_path 上限时截断+哈希，避免 bind 直接炸。"""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return _fit_unix_path(Path(runtime) / "lazygophers" / "browse.sock")
    return _fit_unix_path(Path.home() / ".local" / "state" / "lazygophers"
                          / "scripts" / "browse.sock")


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
    except OSError as exc:
        raise OSError(exc.errno, f"{exc.strerror}: {path}") from None
    finally:
        os.umask(old)
    sock.setblocking(False)
    return sock


# 握手里没带浏览器名时用的槽位名。旧版的通用 wrapper（升级前装的那个）不带
# `--browser`，落到这里 —— 仍然能用，只是多个旧 wrapper 之间还是会互相顶掉。
UNKNOWN_BROWSER = "unknown"


@dataclass
class _Conn:
    """一条已握手的连接。`id` 就是握手时发回去的 connectionId。"""

    id: int
    role: str
    writer: asyncio.StreamWriter
    # native-host：自己代表哪个浏览器（安装时写死在 wrapper 里，或 bridge 场景下
    # 扩展自己在 hello 里报的展示名）。
    # cli：这一次要发给哪个浏览器，空串表示「没指定，你替我挑」。
    browser: str = ""
    # bridge 场景下扩展自己生成的持久实例 ID（`browser-extension/browse/src/
    # native-port.ts` 的 `instanceId()`），装在 chrome.storage.local 里，同一次
    # 安装重连不变。旧 native messaging 的 wrapper 不带这个字段，值就是空串。
    instance: str = ""

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
    # 这条指令发给了哪个浏览器。回包时用它把订阅归到对的那个浏览器名下。
    browser: str = ""


@dataclass
class _Sub:
    """一个对外稳定的订阅。`ext_id` 是扩展当前这一世给的地址，重建后会变。

    `browser` 是它订在哪个浏览器上。**必须记着**：不同浏览器给出的 `ext_id` 可能撞车，
    而且一个浏览器重连时只该重建它自己的订阅，不能把别人的也重下一遍。
    """

    id: str
    cli: _Conn
    params: dict
    browser: str = ""
    ext_id: str = ""


@dataclass
class Daemon:
    """socket 服务端。一个实例一个 socket。"""

    path: Path
    idle_timeout: float = IDLE_TIMEOUT

    _server: asyncio.AbstractServer | None = field(default=None, init=False)
    # 浏览器名 → 它那条 native host 连接。同名的新连接顶掉旧的（同一个浏览器重连），
    # 不同名的互不相干 —— 这正是「Chrome 和 Brave 互踢」那个缺陷的修法。
    _browsers: dict[str, _Conn] = field(default_factory=dict, init=False)
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
        """连续 idle_timeout 没指令、且一个客户端都没连着，就自行退出。

        `idle_timeout <= 0` 是常驻模式（装成系统服务时用的就是它）：永不自退，
        否则服务管理器只会一遍遍把它拉起来。
        """
        if self.idle_timeout <= 0:
            return
        tick = min(30.0, max(0.05, self.idle_timeout / 4))
        while True:
            await asyncio.sleep(tick)
            if self._clis or self._browsers:
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
        want = hello.get("browser")
        instance = hello.get("instanceId")
        conn = _Conn(id=self._conn_seq, role=role, writer=writer,
                     browser=want if isinstance(want, str) else "",
                     instance=instance if isinstance(instance, str) else "")
        if role == ROLE_NATIVE_HOST and not conn.browser:
            # 升级前装的通用 wrapper 不带 `--browser`。不认识就归到 unknown 槽，
            # 照常能用（单浏览器场景和以前一模一样），不让它把 daemon 带倒。
            conn.browser = UNKNOWN_BROWSER
        if conn.role != ROLE_CLI:
            # hello-ack 要带上实际分到的槽位名（可能被 `_browser_slot` 加了数字
            # 后缀消歧义，如 "chromium-2"）——bridge 的 WS 适配层（`lib/browse_
            # bridge.py` 的 `_Adapter`）靠这个字段填 `lg:bridge.connections`。
            # 这一步只算槽位名，不登记、不顶旧连接、不发订阅重建指令：那些要等
            # ack 真发出去、对端确认握手完成后才能做（否则重建订阅会在 ack 之前
            # 抢发一条指令帧，把等 ack 的客户端撞懵）。
            conn.browser = self._browser_slot(conn)
        if not await conn.send(
            {"type": "hello-ack", "connectionId": conn.id, "browser": conn.browser},
            MAX_HELLO_BYTES,
        ):
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
        conn.browser = self._browser_slot(conn)  # 落地为实际分到的槽位名
        old = self._browsers.get(conn.browser)
        self._browsers[conn.browser] = conn
        if old is not None:
            # 只顶掉**同一个实例**的旧连接（它重连了）。别的实例一概不动 ——
            # 以前这里是按浏览器展示名分槽，Chrome 连上会踢掉 Brave，而扩展侧会退避
            # 重连，两者相乘就是无限互踢。
            await _close(old.writer)
            await self._fail_pending(old.id, f"{conn.browser} 的 native host 被新连接顶替")
        await self._rebuild_subscriptions(conn)

    def _browser_slot(self, conn: _Conn) -> str:
        """挑一个不会跟别的活连接**撞身份**的槽位名。

        同一个实例（`instanceId` 相同）重连 → 复用同名槽位，这是正常顶替。
        不同实例撞了同一个展示名（Arc/Chromium 分支猜不出品牌，都报
        "chromium"；或两个 Chrome profile）→ 加数字后缀分开槽位，不互踢。
        旧 native messaging 的 wrapper 不带 instanceId（`conn.instance` 是空串），
        这时退回旧行为：同名即视为同一个，和以前一模一样。
        """
        base = conn.browser
        occupant = self._browsers.get(base)
        if occupant is None or occupant.instance == conn.instance:
            return base
        n = 2
        while True:
            key = f"{base}-{n}"
            occupant = self._browsers.get(key)
            if occupant is None or occupant.instance == conn.instance:
                return key
            n += 1

    async def _detach(self, conn: _Conn) -> None:
        if conn.role == ROLE_CLI:
            self._clis.pop(conn.id, None)
            await self._drop_subscriptions_of(conn)
            for gid in [g for g, p in self._pending.items() if p.cli is conn]:
                del self._pending[gid]
            return
        if self._browsers.get(conn.browser) is conn:
            del self._browsers[conn.browser]
        # 只失败**这一条连接**的在途指令（`_Pending.conn_id` 就是为这个存的），
        # 别的浏览器上跑着的指令不受影响
        await self._fail_pending(conn.id, f"{conn.browser} 的浏览器连接已断开")

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

    def pick_browser(self, want: str = "") -> _Conn:
        """挑出这条指令发给哪个浏览器。挑不出来抛 `_NoBrowser`。

        语义照搬 `lib/profile_store.ProfileStore.resolve()`（archery 用它选站点）：
        显式指定优先 → 只有一个就用那个 → 多个又没指定就报错并列出都有谁。
        """
        if not self._browsers:
            raise _NoBrowser(ERR_NOT_CONNECTED, "浏览器未连接：确认浏览器开着且扩展已启用")
        if want:
            conn = self._browsers.get(want)
            if conn is None:
                known = ", ".join(sorted(self._browsers))
                raise _NoBrowser(ERR_NOT_CONNECTED,
                                 f"没有 {want} 的连接（连着的: {known}）。"
                                 f"确认那个浏览器开着且扩展已启用，或换一个 --browser")
            return conn
        if len(self._browsers) == 1:
            return next(iter(self._browsers.values()))
        known = ", ".join(sorted(self._browsers))
        raise _NoBrowser(ERR_INVALID_ARGUMENT,
                         f"有多个浏览器连着但没指定用哪个（{known}）。加 --browser <名字>")

    async def _from_cli(self, cli: _Conn, message: dict) -> None:
        if "type" in message:
            return  # CLI 只发 Command；没有 type 字段的才是 Command
        self._last_command = time.monotonic()
        if message["method"] == ABORT_METHOD:
            await self._abort(cli, message["id"])
            return
        if message["method"] == BROWSERS_METHOD:
            await cli.send(success(message["id"], {"browsers": sorted(self._browsers)}))
            return
        try:
            # 不排队不等待：浏览器没开就是没开，等下去只会变成一个无人回收的挂起
            browser = self.pick_browser(cli.browser)
        except _NoBrowser as exc:
            await cli.send(error(message["id"], exc.code, str(exc)))
            return

        method, params, cli_id = message["method"], message["params"], message["id"]
        forward = self._to_ext_subscription(params) if method == UNSUBSCRIBE_METHOD else params
        self._seq += 1
        gid = self._seq
        self._pending[gid] = _Pending(cli=cli, cli_id=cli_id, conn_id=browser.id,
                                      method=method, params=params, browser=browser.browser)
        if not await browser.send(command(gid, method, forward),
                                  limit=MAX_OUTGOING_FRAME_BYTES):
            del self._pending[gid]
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
            if entry.cli is not None:
                await entry.cli.send(error(entry.cli_id, ERR_ABORTED, why))
        await cli.send(success(cli_id, {"aborted": count}))

    async def _from_browser(self, conn: _Conn, message: dict) -> None:
        kind = message.get("type")
        if kind == "event":
            await self._fanout(conn, message)
            return
        if kind is None:
            # 扩展不再反过来问 daemon 任何事（策略和审计都在它自己那儿），所以这种帧
            # 现在是个错误，不是一条要路由的消息。
            await conn.send(error(message.get("id", 0), ERR_UNKNOWN_COMMAND,
                                  f"daemon 不处理 {message.get('method')!r}：策略和审计都在插件里"))
            return
        if kind not in ("success", "error"):
            return
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

    async def _fanout(self, conn: _Conn, message: dict) -> None:
        if message["method"] == KEEPALIVE_METHOD:
            return
        subs = message["params"].get("subscriptions")
        if not isinstance(subs, list) or not subs:
            for cli in list(self._clis.values()):
                await cli.send(message)
            return
        # 带订阅标记的事件只送给订阅它的那个 CLI，并把扩展的内部 id 换回对外的。
        # 按发来的那个浏览器查，不然两个浏览器给出同一个 ext_id 时会串台。
        targets: dict[int, tuple[_Conn, list[str]]] = {}
        for ext_id in subs:
            sub = self._sub_by_ext(ext_id, conn.browser)
            if sub is None:
                continue
            targets.setdefault(sub.cli.id, (sub.cli, []))[1].append(sub.id)
        for cli, ids in targets.values():
            await cli.send({**message, "params": {**message["params"], "subscriptions": ids}})

    # ------------------------------------------------------------ 订阅
    def _sub_by_ext(self, ext_id, browser: str) -> _Sub | None:
        return next((s for s in self._subs.values()
                     if s.ext_id == ext_id and s.browser == browser), None)

    def _to_ext_subscription(self, params: dict) -> dict:
        """把对外的 `sub-N` 换成扩展当前认的 id。不认识就原样透传，让扩展去报错。"""
        sub = self._subs.get(params.get("subscription"))
        return params if sub is None else {**params, "subscription": sub.ext_id}

    def _track_subscription(self, entry: _Pending, out: dict) -> dict:
        result = out.get("result", {})
        if entry.method == SUBSCRIBE_METHOD and isinstance(result.get("subscription"), str):
            self._sub_seq += 1
            sub = _Sub(id=f"sub-{self._sub_seq}", cli=entry.cli, params=dict(entry.params),
                       browser=entry.browser, ext_id=result["subscription"])
            self._subs[sub.id] = sub
            return {**out, "result": {**result, "subscription": sub.id}}
        if entry.method == UNSUBSCRIBE_METHOD and isinstance(result.get("removed"), list):
            removed = []
            for ext_id in result["removed"]:
                sub = self._sub_by_ext(ext_id, entry.browser)
                removed.append(ext_id if sub is None else self._subs.pop(sub.id).id)
            return {**out, "result": {**result, "removed": removed}}
        return out

    async def _rebuild_subscriptions(self, conn: _Conn) -> None:
        """扩展重连后重下一遍订阅。

        订阅活在 service worker 内存里，浏览器重启或扩展重载后必然没了；CLI 那边还
        举着 `sub-N` 在等事件，所以补齐这件事只能由 daemon 做。

        **只重建这个浏览器自己的订阅。** Chrome 重连时把 Brave 的订阅也重下一遍，
        等于悄悄把订阅搬了家。
        """
        for sub in [s for s in self._subs.values() if s.browser == conn.browser]:
            self._seq += 1
            gid = self._seq
            self._pending[gid] = _Pending(cli=None, cli_id=0, conn_id=conn.id,
                                          method=SUBSCRIBE_METHOD, params=sub.params,
                                          sub_id=sub.id, browser=conn.browser)
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
        # 尽力回收，扩展那边不然会一直挂着 webRequest 监听。每条订阅回收到**它自己那个
        # 浏览器**上 —— 发错地方轻则报错，重则退掉别人一条同名的订阅。
        for sub in gone:
            browser = self._browsers.get(sub.browser)
            if browser is None:
                continue
            self._seq += 1
            self._pending[self._seq] = _Pending(cli=None, cli_id=0, conn_id=browser.id,
                                                method=UNSUBSCRIBE_METHOD, sub_id=None,
                                                browser=browser.browser)
            await browser.send(
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
async def connect(role: str, path: Path | None = None, *,
                  browser: str = "", instance: str = "") -> tuple[asyncio.StreamReader, asyncio.StreamWriter, int]:
    """连上 daemon 并握手，返回 (reader, writer, connectionId)。

    T03 的 native host 用 `role="native-host"`，T04 的 CLI 用 `role="cli"`。
    """
    if role not in ROLES:
        raise ProtocolError(f"role 只能是 {sorted(ROLES)}: {role!r}")
    target = socket_path() if path is None else path
    reader, writer = await asyncio.open_unix_connection(str(target))
    # native-host：我代表哪个浏览器（安装时写死在 wrapper 里），`instance` 是扩展
    # 侧持久生成的实例 ID，用来在展示名撞车时（Arc/Chromium 都报 "chromium"）分开
    # 槽位而不互踢，见 `Daemon._browser_slot`。
    # cli：这一次发给哪个浏览器，空串 = 让 daemon 替我挑。
    hello = {"type": "hello", "role": role}
    if browser:
        hello["browser"] = browser
    if instance:
        hello["instanceId"] = instance
    writer.write(pack(hello, MAX_HELLO_BYTES))
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
    "ABORT_METHOD",
    "BROWSERS_METHOD",
    "UNKNOWN_BROWSER",
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

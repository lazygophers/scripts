"""browse bridge：daemon + 一个收扩展 WebSocket 的监听，唯一连接真相。

2026-09-15 连接层重做（用户批准，证据与方案见 `browser-extension/reports/trae-bridge-plan.html`）：
native messaging 链路（浏览器 fork wrapper → stdin/stdout → unix socket）整体换成
「扩展 service worker 直连本地 bridge 的 WebSocket」。对齐本机 Trae CN 的实证结构
（`browser-bridge` 常驻服务 + 本机回环端口 + heartbeat + 连接表）。

    CLI ──unix socket──▶ Bridge ──WebSocket(127.0.0.1)──▶ 各浏览器扩展

**daemon 的路由、订阅重建、abort 一行不改**：每条 WS 连接经 `socketpair` 适配成
daemon 已经认识的流——WS 文本帧（一条 JSON）↔ 4 字节长度前缀帧。bridge 只加三样：

1. WS 监听（只绑 127.0.0.1，Origin 白名单 = 我们的扩展 ID）
2. 每条连接的元数据（浏览器名、连入时间、最后活跃），`lg:bridge.connections` 查询
3. `browse bridge` 的启动入口

多浏览器并存：连接按 connectionId 隔离，daemon 侧仍按展示名分槽，但这个名字
现在由**扩展自己**在 hello 里报，不再靠 wrapper 猜父进程。Chrome 和 Arc 都可能
报 "chromium"（Arc 不在 `userAgentData.brands` 里报自己）——`Daemon._browser_slot`
靠扩展持久生成的 `instanceId` 分辨这是不是同一个实例重连，不是就加数字后缀
（"chromium"、"chromium-2"）分开槽位，不再互踢。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from lib import browse_ws
from lib.browse_daemon import (
    BROWSERS_METHOD,
    IDLE_TIMEOUT,
    ROLE_NATIVE_HOST,
    Daemon,
    pack,
    read_frame,
    socket_path as daemon_socket_path,
)
from lib.browse_install import EXTENSION_IDS
from lib.browse_protocol import MAX_INCOMING_FRAME_BYTES, success

# 扩展连 bridge 的固定端口。扩展读不到本地文件，端口只能双方写死成常量
# （Python 侧允许环境变量覆盖，给测试和端口冲突留口子）。
DEFAULT_WS_PORT = 9330

# CLI 查 bridge 连接表用的方法名（和 ABORT/BROWSERS 一样，bridge 本地执行不转发）。
CONNECTIONS_METHOD = "lg:bridge.connections"

# bridge 每 30 秒没收到扩展任何消息（心跳是每 10-20 秒一条）就认为它死了，断开。
WS_SILENCE_TIMEOUT = 30.0

# 扩展 hello 里的 role。daemon 的词汇表里这个角色叫 native-host（历史名），这里
# 翻译一下，扩展侧就不用背着旧名字。
ROLE_EXTENSION = "extension"


def ws_port() -> int:
    return int(os.environ.get("BROWSE_BRIDGE_PORT", DEFAULT_WS_PORT))


def _origin_allowed(origin: str | None) -> bool:
    """白名单：我们的扩展 ID；没有 Origin 的（本机非浏览器客户端，测试）也放行。

    威胁口径与旧 native messaging 的 allowed_origins 相同：拦的是「别的网页/扩展
    连进来」，拦不了本机进程伪造 Origin——本地代码执行的攻击者本来就能直接读
    socket，这不是这一层要解决的问题。
    """
    if origin is None:
        return True
    return origin in {f"chrome-extension://{i}" for i in EXTENSION_IDS}


@dataclass
class Bridge(Daemon):
    """daemon（CLI 侧 unix socket）+ 扩展侧 WebSocket 监听。"""

    ws_port: int = field(default_factory=ws_port)
    _ws_server: asyncio.AbstractServer | None = field(default=None, init=False)
    # daemon connId → {browser, since, lastSeen}。`browse status` 的数据源。
    _ws_meta: dict[int, dict] = field(default_factory=dict, init=False)

    # ------------------------------------------------------------ 生命周期
    async def start(self) -> bool:
        if not await super().start():
            return False
        self._ws_server = await asyncio.start_server(
            self._handle_ws, "127.0.0.1", self.ws_port)
        return True

    async def stop(self) -> None:
        if self._ws_server is not None:
            self._ws_server.close()
            # 3.13 的 wait_closed 会等所有 handler 结束，还连着的扩展会让它永远等。
            # 服务已经不接受新连接了，残留的 handler 随进程退出收尾。
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._ws_server.wait_closed(), 2.0)
            self._ws_server = None
        await super().stop()

    # ------------------------------------------------------------ WS 侧
    async def _handle_ws(self, reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter) -> None:
        try:
            await browse_ws.server_handshake(reader, writer, origin_allowed=_origin_allowed)
            await self._adopt_ws(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError, browse_ws.WsClosed,
                OSError, json.JSONDecodeError, ValueError):
            pass  # 握手被拒 / 对端断开 / 坏消息 —— 这条连接不要了，不拖垮 bridge
        finally:
            with contextlib.suppress(OSError):
                writer.close()

    async def _adopt_ws(self, ws_reader: asyncio.StreamReader,
                        ws_writer: asyncio.StreamWriter) -> None:
        """把一条 WS 连接适配成 daemon 的流，然后把 daemon 的处理直接挂上去。"""
        # socketpair 两端各接成 asyncio 流：一端给 daemon，一端给适配泵
        ours, theirs = socket.socketpair()
        ours.setblocking(False)
        theirs.setblocking(False)
        daemon_reader, daemon_writer = await asyncio.open_connection(sock=ours, limit=browse_ws.MAX_HANDSHAKE_BYTES)
        pump_reader, pump_writer = await asyncio.open_connection(sock=theirs, limit=browse_ws.MAX_HANDSHAKE_BYTES)

        adapter = _Adapter(ws_reader=ws_reader, ws_writer=ws_writer,
                           sock_reader=pump_reader, sock_writer=pump_writer,
                           meta=self._ws_meta)
        daemon_task = asyncio.create_task(super()._handle(daemon_reader, daemon_writer))
        try:
            await adapter.pump()
        finally:
            # 不 cancel daemon 任务：它在 finally 里的 _detach 负责把在途指令
            # 失败回 CLI，cancel 会把那一步掐掉、让 CLI 挂到超时。关掉两端流，
            # daemon 的 _pump 看到 EOF 自然退出，detach 走完。
            with contextlib.suppress(OSError):
                pump_writer.close()
                daemon_writer.close()
            with contextlib.suppress(asyncio.CancelledError, ConnectionError,
                                     asyncio.IncompleteReadError, OSError):
                await daemon_task

    # ------------------------------------------------------------ CLI 侧本地方法
    async def _from_cli(self, cli, message: dict) -> None:
        if "type" not in message and message.get("method") == CONNECTIONS_METHOD:
            now = time.monotonic()
            rows = [{
                "connectionId": cid,
                "browser": meta["browser"],
                "sinceSeconds": int(now - meta["since"]),
                "idleSeconds": int(now - meta["lastSeen"]),
            } for cid, meta in self._ws_meta.items()]
            # 只列还挂着的连接（daemon 侧断开时 _ws_meta 的清理由适配泵的 finally 做）
            await cli.send(success(message["id"], {"connections": rows}))
            return
        await super()._from_cli(cli, message)


@dataclass
class _Adapter:
    """双向泵：WS 文本帧（一条 JSON）↔ daemon 的 4 字节长度前缀帧。

    顺带干两件小事：
    - 扩展 hello 的 `role: "extension"` 翻译成 daemon 的 `native-host`
    - 记录这条连接的元数据（hello-ack 里的 connectionId 是身份）
    """

    ws_reader: asyncio.StreamReader
    ws_writer: asyncio.StreamWriter
    sock_reader: asyncio.StreamReader
    sock_writer: asyncio.StreamWriter
    meta: dict[int, dict]

    conn_id: int | None = field(default=None, init=False)

    async def pump(self) -> None:
        try:
            await asyncio.gather(self._ws_to_sock(), self._sock_to_ws())
        finally:
            if self.conn_id is not None:
                self.meta.pop(self.conn_id, None)

    async def _ws_to_sock(self) -> None:
        first = True
        while True:
            text = await asyncio.wait_for(
                browse_ws.read_message(self.ws_reader, self.ws_writer, MAX_INCOMING_FRAME_BYTES),
                timeout=WS_SILENCE_TIMEOUT)
            message = json.loads(text)
            if first and message.get("role") == ROLE_EXTENSION:
                message["role"] = ROLE_NATIVE_HOST
                first = False
            self._touch(message)
            self.sock_writer.write(pack(message))
            await self.sock_writer.drain()

    async def _sock_to_ws(self) -> None:
        while True:
            message = await read_frame(self.sock_reader, MAX_INCOMING_FRAME_BYTES)
            if message.get("type") == "hello-ack" and self.conn_id is None:
                self.conn_id = message.get("connectionId")
                # daemon 分槽（可能加了 "-2" 后缀消歧义，见 `Daemon._browser_slot`）
                # 后才知道实际槽位名，所以优先用 hello-ack 里带回来的这个，不是
                # `_touch` 记的原始展示名。
                browser = message.get("browser")
                self.meta[self.conn_id] = {
                    "browser": browser if isinstance(browser, str) and browser else self._browser,
                    "since": time.monotonic(),
                    "lastSeen": time.monotonic(),
                }
            await browse_ws.send_text(self.ws_writer, json.dumps(message, ensure_ascii=False))

    # hello 里的 browser 名，翻译后的第一条（hello）记下；之后的消息只刷新 lastSeen
    _browser: str = ""

    def _touch(self, message: dict) -> None:
        if self.conn_id is None and message.get("type") == "hello":
            browser = message.get("browser")
            self._browser = browser if isinstance(browser, str) and browser else "chromium"
            return
        entry = self.meta.get(self.conn_id or -1)
        if entry is not None:
            entry["lastSeen"] = time.monotonic()


async def run(path: Path | None = None, idle_timeout: float | None = None,
              port: int | None = None) -> bool:
    """起 bridge 并守到它退出。已经有一个在跑就直接返回 False。"""
    from lib.browse_daemon import IDLE_TIMEOUT
    bridge = Bridge(path=daemon_socket_path() if path is None else path,
                    idle_timeout=IDLE_TIMEOUT if idle_timeout is None else idle_timeout,
                    ws_port=ws_port() if port is None else port)
    if not await bridge.start():
        return False
    try:
        await bridge.wait_stopped()
    finally:
        await bridge.stop()
    return True


__all__ = [
    "BROWSERS_METHOD",
    "CONNECTIONS_METHOD",
    "DEFAULT_WS_PORT",
    "ROLE_EXTENSION",
    "WS_SILENCE_TIMEOUT",
    "Bridge",
    "run",
    "ws_port",
]

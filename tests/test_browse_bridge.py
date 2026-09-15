"""browse_bridge：bridge 的 WS 适配 + 多浏览器路由 + 连接表，端到端在回环上跑。

假扩展用 `browse_ws.client_connect` 扮（role=extension），假 CLI 用
`browse_daemon.connect` 走真实 unix socket 协议。bridge 全程真实：真实 socketpair、
真实双向泵、真实 daemon 路由。
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib import browse_bridge, browse_install, browse_ws
from lib.browse_daemon import connect as cli_connect, pack, read_frame
from lib.browse_protocol import command


def _ws_recv(reader, writer) -> asyncio.Future:
    """异步读一条 WS 消息（JSON）。"""
    return asyncio.ensure_future(browse_ws.read_message(reader, writer, 1 << 20))


async def _ext_connect(port: int, browser: str, instance: str = "") -> tuple:
    """假扩展：握手 + hello，返回 (reader, writer, connectionId)。"""
    reader, writer = await browse_ws.client_connect("127.0.0.1", port)
    hello = {"type": "hello", "role": "extension", "browser": browser}
    if instance:
        hello["instanceId"] = instance
    await browse_ws.client_send(writer, json.dumps(hello))
    ack = json.loads(await browse_ws.read_message(reader, writer, 1 << 20))
    assert ack["type"] == "hello-ack", ack
    return reader, writer, ack["connectionId"]


async def _ext_connect_with_origin(port: int, browser: str, origin: str) -> tuple:
    """同 `_ext_connect`，但走真实 Origin 握手校验（不是走「无 Origin 免检」的口子）。"""
    reader, writer = await browse_ws.client_connect("127.0.0.1", port, origin=origin)
    await browse_ws.client_send(writer, json.dumps({"type": "hello", "role": "extension", "browser": browser}))
    ack = json.loads(await browse_ws.read_message(reader, writer, 1 << 20))
    assert ack["type"] == "hello-ack", ack
    return reader, writer, ack["connectionId"]


class BridgeCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sock_path = pathlib.Path(self._tmp.name) / "bridge.sock"

    def run_bridge(self, body) -> object:
        async def scenario():
            bridge = browse_bridge.Bridge(path=self.sock_path, ws_port=0)
            self.assertTrue(await bridge.start())
            try:
                port = bridge._ws_server.sockets[0].getsockname()[1]
                return await body(port)
            finally:
                await bridge.stop()
        return asyncio.run(scenario())

    # ---------------------------------------------------------------- 用例

    def test_cli_command_routed_to_extension_and_back(self) -> None:
        """整条链：CLI →(unix)→ bridge →(WS)→ 扩展，回包原路带回、id 换回 CLI 的。"""

        async def body(port: int) -> dict:
            er, ew, _ = await _ext_connect(port, "chrome")
            cr, cw, _ = await cli_connect("cli", self.sock_path)
            cw.write(pack(command(1, "script.evaluate", {"expression": "1+1"})))
            await cw.drain()
            got = json.loads(await browse_ws.read_message(er, ew, 1 << 20))
            await browse_ws.client_send(ew, json.dumps(
                {"type": "success", "id": got["id"], "result": {"value": 2}}))
            reply = await read_frame(cr, 1 << 20)
            ew.close(); cw.close()
            return reply
        reply = self.run_bridge(body)
        self.assertEqual(reply, {"type": "success", "id": 1, "result": {"value": 2}})

    def test_two_browsers_isolated_and_routed(self) -> None:
        """多浏览器并存：两条扩展连接互不干扰；--browser（CLI hello）指定发给谁。"""

        async def body(port: int) -> tuple[bool, dict]:
            r1, w1, _ = await _ext_connect(port, "chrome")
            r2, w2, _ = await _ext_connect(port, "arc")
            # 指定发给 arc 的连接
            cr, cw, _ = await cli_connect("cli", self.sock_path, browser="arc")
            cw.write(pack(command(7, "script.evaluate", {"expression": "who"})))
            await cw.drain()
            m2 = json.loads(await browse_ws.read_message(r2, w2, 1 << 20))
            await browse_ws.client_send(w2, json.dumps(
                {"type": "success", "id": m2["id"], "result": {}}))
            reply = await read_frame(cr, 1 << 20)
            # chrome 那条不该收到任何东西：等 0.2 秒，等到了就是串台
            try:
                leak = await asyncio.wait_for(
                    browse_ws.read_message(r1, w1, 1 << 20), timeout=0.2)
            except asyncio.TimeoutError:
                leak = None
            w1.close(); w2.close(); cw.close()
            return leak is None, reply
        isolated, reply = self.run_bridge(body)
        self.assertTrue(isolated, "chrome 连接收到了发给 arc 的指令")
        self.assertEqual(reply["id"], 7)

    def test_same_display_name_different_instances_do_not_evict_each_other(self) -> None:
        """Chrome 和 Arc 都可能报 "chromium"——不同 instanceId 时必须各开一个槽位。"""

        async def body(port: int) -> dict:
            r1, w1, _ = await _ext_connect(port, "chromium", instance="chrome-uuid")
            r2, w2, _ = await _ext_connect(port, "chromium", instance="arc-uuid")
            cr, cw, _ = await cli_connect("cli", self.sock_path)
            cw.write(pack(command(1, browse_bridge.CONNECTIONS_METHOD, {})))
            await cw.drain()
            reply = await read_frame(cr, 1 << 20)
            # 第一条连接还活着才算没被顶替：发个指令给它验证
            cw2 = None
            try:
                cr2, cw2, _ = await cli_connect("cli", self.sock_path, browser="chromium")
                cw2.write(pack(command(2, "script.evaluate", {})))
                await cw2.drain()
                m1 = json.loads(await browse_ws.read_message(r1, w1, 1 << 20))
                first_alive = bool(m1.get("id"))
            except (ConnectionError, OSError):
                first_alive = False
            finally:
                w1.close(); w2.close(); cw.close()
                if cw2 is not None:
                    cw2.close()
            return reply["result"], first_alive
        result, first_alive = self.run_bridge(body)
        self.assertTrue(first_alive, "同名不同实例的旧连接被顶替了")
        self.assertEqual(sorted(row["browser"] for row in result["connections"]),
                         ["chromium", "chromium-2"])

    def test_same_instance_reconnect_still_replaces_the_old_slot(self) -> None:
        """同一个 instanceId 重连——旧行为不能退化：还是当场顶替旧连接。"""

        async def body(port: int) -> bool:
            r1, w1, _ = await _ext_connect(port, "chrome", instance="same-uuid")
            r2, w2, _ = await _ext_connect(port, "chrome", instance="same-uuid")
            cr, cw, _ = await cli_connect("cli", self.sock_path)
            cw.write(pack(command(1, browse_bridge.CONNECTIONS_METHOD, {})))
            await cw.drain()
            reply = await read_frame(cr, 1 << 20)
            w2.close(); cw.close()
            return reply["result"]["connections"]
        connections = self.run_bridge(body)
        self.assertEqual([row["browser"] for row in connections], ["chrome"])

    def test_connections_table(self) -> None:
        """lg:bridge.connections 列出每条连接的浏览器名和活跃秒数。"""

        async def body(port: int) -> dict:
            _, w1, _ = await _ext_connect(port, "chrome")
            _, w2, _ = await _ext_connect(port, "arc")
            cr, cw, _ = await cli_connect("cli", self.sock_path)
            cw.write(pack(command(1, browse_bridge.CONNECTIONS_METHOD, {})))
            await cw.drain()
            reply = await read_frame(cr, 1 << 20)
            w1.close(); w2.close(); cw.close()
            return reply["result"]
        result = self.run_bridge(body)
        browsers = sorted(row["browser"] for row in result["connections"])
        self.assertEqual(browsers, ["arc", "chrome"])
        for row in result["connections"]:
            self.assertLessEqual(row["idleSeconds"], 2)

    def test_extension_drop_fails_pending(self) -> None:
        """扩展断了，在途指令当场失败（not connected），CLI 不挂起。"""

        async def body(port: int) -> dict:
            er, ew, _ = await _ext_connect(port, "chrome")
            cr, cw, _ = await cli_connect("cli", self.sock_path)
            cw.write(pack(command(1, "script.evaluate", {"expression": "x"})))
            await cw.drain()
            got = json.loads(await browse_ws.read_message(er, ew, 1 << 20))
            assert got.get("id"), got  # 指令已到扩展，还没回
            ew.close()
            reply = await read_frame(cr, 1 << 20)
            cw.close()
            return reply
        reply = self.run_bridge(body)
        self.assertEqual(reply["type"], "error")
        self.assertEqual(reply["error"], "lg:browser not connected")

    def test_foreign_origin_rejected(self) -> None:
        """别的网页 Origin 一律 403，连不上。"""

        async def body(port: int) -> str:
            try:
                await browse_ws.client_connect("127.0.0.1", port, origin="http://evil.test")
                return "connected"
            except ConnectionError as exc:
                return str(exc)
        self.assertIn("403", self.run_bridge(body))

    def test_real_extension_origin_accepted(self) -> None:
        """真扩展发的 Origin 不带末尾斜杠（RFC 6454），白名单必须认得这个格式。"""

        async def body(port: int) -> str:
            origin = f"chrome-extension://{browse_install.EXTENSION_IDS[0]}"
            _, w, _ = await _ext_connect_with_origin(port, "chrome", origin)
            w.close()
            return "connected"
        self.assertEqual(self.run_bridge(body), "connected")


if __name__ == "__main__":
    unittest.main()

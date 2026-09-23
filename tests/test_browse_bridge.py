"""browse_bridge：bridge 的 WS 适配 + 多浏览器路由 + 连接表，端到端在回环上跑。

假扩展用 `browse_ws.client_connect` 扮（role=extension），假 CLI 用
`browse_daemon.connect` 走真实 unix socket 协议。bridge 全程真实：真实 socketpair、
真实双向泵、真实 daemon 路由。
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import socket
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib import browse_bridge, browse_install, browse_log, browse_ws
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

    def run_bridge(self, body, silence: float | None = None) -> object:
        async def scenario():
            kwargs = {} if silence is None else {"ws_silence": silence}
            bridge = browse_bridge.Bridge(path=self.sock_path, ws_port=0, **kwargs)
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


class LogCase(unittest.TestCase):
    """服务端日志：写、轮转、读回。文件路径全程指到临时目录，不碰真实落点。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = pathlib.Path(self._tmp.name) / "bridge.log"

    def test_record_then_tail_returns_entries_oldest_first(self) -> None:
        browse_log.record("ws.open", path=self.path, browser="chrome")
        browse_log.record("ws.close", path=self.path, browser="chrome", seconds=3)
        lines = browse_log.tail(10, path=self.path)
        self.assertEqual([entry["event"] for entry in lines], ["ws.open", "ws.close"])
        self.assertEqual(lines[1]["seconds"], 3)
        self.assertIsInstance(lines[0]["at"], float)

    def test_tail_limit_keeps_the_newest(self) -> None:
        for i in range(10):
            browse_log.record("tick", path=self.path, i=i)
        lines = browse_log.tail(3, path=self.path)
        self.assertEqual([entry["i"] for entry in lines], [7, 8, 9])

    def test_tail_caps_the_limit_and_survives_a_missing_file(self) -> None:
        self.assertEqual(browse_log.tail(10, path=self.path), [])
        for i in range(browse_log.MAX_TAIL + 5):
            browse_log.record("tick", path=self.path, i=i)
        self.assertEqual(len(browse_log.tail(10_000, path=self.path)), browse_log.MAX_TAIL)

    def test_a_truncated_line_is_skipped_not_fatal(self) -> None:
        browse_log.record("ws.open", path=self.path)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write('{"at": 1, "event": "half\n')
        browse_log.record("ws.close", path=self.path)
        self.assertEqual([entry["event"] for entry in browse_log.tail(10, path=self.path)],
                         ["ws.open", "ws.close"])

    def test_other_loggers_are_filtered_out_of_tail(self) -> None:
        # 统一日志文件里还有别的工具在写，tail 只取 browse-daemon 的行
        from lib import log as _log
        _log.record("cli.start", logger="browse", target=self.path)
        browse_log.record("ws.open", path=self.path)
        self.assertEqual([entry["event"] for entry in browse_log.tail(10, path=self.path)],
                         ["ws.open"])

    def test_a_write_failure_never_raises(self) -> None:
        # 落点是个目录：写文件必然失败，但 bridge 不能因为记日志而倒下
        import logging
        blocked = pathlib.Path(self._tmp.name) / "as-a-dir"
        blocked.mkdir()
        with unittest.mock.patch.object(logging, "raiseExceptions", False):  # 别往 stderr 倒噪声
            browse_log.record("ws.open", path=blocked)  # 不抛就算过

    def test_log_path_follows_the_environment_override(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"BROWSE_BRIDGE_LOG": "/tmp/x.log",
                                                   "SCRIPTS_LOG": ""}):
            self.assertEqual(browse_log.log_path(), pathlib.Path("/tmp/x.log"))
        import tempfile
        # tests/__init__ 会全局设 SCRIPTS_LOG 指向套件临时文件，这里要验的是
        # 「两个覆盖都没有时的默认落点」，所以连 SCRIPTS_LOG 一起清掉
        with unittest.mock.patch.dict(os.environ, {"BROWSE_BRIDGE_LOG": "", "SCRIPTS_LOG": ""},
                                      clear=False), \
             unittest.mock.patch.object(tempfile, "tempdir", None):  # gettempdir 有缓存
            os.environ.pop("BROWSE_BRIDGE_LOG", None)
            os.environ.pop("SCRIPTS_LOG", None)
            self.assertEqual(browse_log.log_path(),
                             pathlib.Path(tempfile.gettempdir()) / "lazygophers" / "scripts.log")


class BridgeIntrospectionCase(BridgeCase):
    """`lg:bridge.info` / `lg:bridge.log`：CLI 和扩展两个方向都问得到。"""

    def setUp(self) -> None:
        super().setUp()
        self.log_path = pathlib.Path(self._tmp.name) / "bridge.log"
        patch = unittest.mock.patch.dict(os.environ, {"BROWSE_BRIDGE_LOG": str(self.log_path),
                                               "SCRIPTS_LOG": ""})
        patch.start()
        self.addCleanup(patch.stop)

    def test_cli_asks_for_bridge_info(self) -> None:
        async def body(port: int) -> dict:
            # 握着扩展那条连接：撒手就会被回收，连接一断连接表立刻空掉
            er, ew, _ = await _ext_connect(port, "chrome")
            cr, cw, _ = await cli_connect("cli", self.sock_path)
            cw.write(pack(command(1, browse_bridge.INFO_METHOD, {})))
            await cw.drain()
            reply = await read_frame(cr, 1 << 20)
            ew.close(); cw.close()
            return reply
        reply = self.run_bridge(body)
        info = reply["result"]
        self.assertEqual(info["socket"], str(self.sock_path))
        self.assertEqual(info["logPath"], str(self.log_path))
        self.assertEqual(len(info["connections"]), 1)
        self.assertEqual(info["connections"][0]["browser"], "chrome")
        self.assertGreater(info["pid"], 0)

    def test_the_extension_may_read_info_and_log(self) -> None:
        """面板要显示服务情况，所以扩展这个方向也得能问这两条。"""

        async def body(port: int) -> tuple[dict, dict]:
            er, ew, _ = await _ext_connect(port, "chrome")
            await browse_ws.client_send(ew, json.dumps(
                command(11, browse_bridge.INFO_METHOD, {})))
            info = json.loads(await browse_ws.read_message(er, ew, 1 << 20))
            await browse_ws.client_send(ew, json.dumps(
                command(12, browse_bridge.LOG_METHOD, {"limit": 5})))
            logs = json.loads(await browse_ws.read_message(er, ew, 1 << 20))
            ew.close()
            return info, logs
        info, logs = self.run_bridge(body)
        self.assertEqual(info["id"], 11)
        self.assertEqual(info["type"], "success")
        self.assertGreater(info["result"]["port"], 0)
        self.assertEqual(logs["id"], 12)
        # 这条连接自己接上来那一下就该在日志里
        self.assertIn("ws.open", [entry["event"] for entry in logs["result"]["lines"]])

    def test_the_extension_still_cannot_ask_anything_else(self) -> None:
        """只开了两条只读方法的口子，别的照旧拒掉。"""

        async def body(port: int) -> dict:
            er, ew, _ = await _ext_connect(port, "chrome")
            await browse_ws.client_send(ew, json.dumps(
                command(13, "lg:config.set", {"confirm_mode": "silent"})))
            reply = json.loads(await browse_ws.read_message(er, ew, 1 << 20))
            ew.close()
            return reply
        reply = self.run_bridge(body)
        self.assertEqual(reply["type"], "error")
        self.assertEqual(reply["id"], 13)

    def test_connections_and_lifecycle_land_in_the_log(self) -> None:
        async def body(port: int) -> None:
            er, ew, _ = await _ext_connect(port, "chrome")
            ew.close()
            await asyncio.sleep(0.1)  # 让适配泵的 finally 跑完
        self.run_bridge(body)
        events = [entry["event"] for entry in browse_log.tail(50, path=self.log_path)]
        self.assertEqual(events[0], "bridge.start")
        self.assertIn("ws.open", events)
        self.assertIn("ws.close", events)
        self.assertEqual(events[-1], "bridge.stop")


class TransportCase(BridgeCase):
    """传输层异常路径（静默超时 / 畸形 JSON / close 帧 / 握手前断开），端到端。"""

    def setUp(self) -> None:
        super().setUp()
        self.log_path = pathlib.Path(self._tmp.name) / "bridge.log"
        patch = unittest.mock.patch.dict(os.environ, {"BROWSE_BRIDGE_LOG": str(self.log_path),
                                               "SCRIPTS_LOG": ""})
        patch.start()
        self.addCleanup(patch.stop)

    def _events(self) -> list[dict]:
        return browse_log.tail(100, path=self.log_path)

    def test_silence_timeout_drops_connection(self) -> None:
        """30 秒（测试注入 0.4 秒）收不到扩展任何消息就断开，连接表不残留。"""

        async def body(port: int) -> dict:
            er, ew, _ = await _ext_connect(port, "chrome")
            ew.write(browse_ws.encode_frame(  # 骗 lastSeen 之后立刻装死
                browse_ws.OP_PONG, b"", mask=True))
            await ew.drain()
            await asyncio.sleep(1.0)  # > 注入的 0.4s 静默超时
            cr, cw, _ = await cli_connect("cli", self.sock_path)
            cw.write(pack(command(1, browse_bridge.CONNECTIONS_METHOD, {})))
            await cw.drain()
            reply = await read_frame(cr, 1 << 20)
            ew.close(); cw.close()
            return reply["result"]
        result = self.run_bridge(body, silence=0.4)
        self.assertEqual(result["connections"], [], "静默连接没有断开")
        dropped = [e for e in self._events() if e["event"] == "ws.dropped"]
        self.assertTrue(dropped, "静默断开没有记日志")
        self.assertEqual(dropped[0]["reason"], "TimeoutError")

    def test_malformed_json_drops_and_fails_pending(self) -> None:
        """非法 JSON 文本帧：连接断开、记一笔、在途指令当场失败不挂起。"""

        async def body(port: int) -> dict:
            er, ew, _ = await _ext_connect(port, "chrome")
            cr, cw, _ = await cli_connect("cli", self.sock_path)
            cw.write(pack(command(1, "script.evaluate", {"expression": "x"})))
            await cw.drain()
            got = json.loads(await browse_ws.read_message(er, ew, 1 << 20))
            assert got.get("id"), got  # 指令已到扩展
            ew.write(browse_ws.encode_frame(browse_ws.OP_TEXT, b"not-json{", mask=True))
            await ew.drain()
            reply = await read_frame(cr, 1 << 20)
            ew.close(); cw.close()
            return reply
        reply = self.run_bridge(body)
        self.assertEqual(reply["type"], "error")
        dropped = [e for e in self._events() if e["event"] == "ws.dropped"]
        self.assertEqual(dropped[0]["reason"], "JSONDecodeError")

    def test_close_frame_terminates_both_pumps(self) -> None:
        """扩展发 close 帧：双向泵都终止，在途指令收到错误回包。"""

        async def body(port: int) -> dict:
            er, ew, _ = await _ext_connect(port, "chrome")
            cr, cw, _ = await cli_connect("cli", self.sock_path)
            cw.write(pack(command(1, "script.evaluate", {"expression": "x"})))
            await cw.drain()
            await browse_ws.read_message(er, ew, 1 << 20)  # 指令已到扩展
            ew.write(browse_ws.encode_frame(browse_ws.OP_CLOSE, b"", mask=True))
            await ew.drain()
            reply = await read_frame(cr, 1 << 20)
            ew.close(); cw.close()
            return reply
        reply = self.run_bridge(body)
        self.assertEqual(reply["type"], "error")
        dropped = [e for e in self._events() if e["event"] == "ws.dropped"]
        self.assertEqual(dropped[0]["reason"], "WsClosed")

    def test_disconnect_without_or_after_hello_leaks_no_meta(self) -> None:
        """断开路径（握手后不发 hello / hello 完成后撒手）都不在连接表留尸体。"""

        async def body(port: int) -> list[int]:
            # 路径一：握手完成、hello 没发就断
            r, w = await browse_ws.client_connect("127.0.0.1", port)
            w.close()
            await asyncio.sleep(0.1)
            # 路径二：hello 完成后撒手
            er, ew, _ = await _ext_connect(port, "chrome")
            ew.close()
            await asyncio.sleep(0.1)  # 让适配泵的 finally 跑完
            cr, cw, _ = await cli_connect("cli", self.sock_path)
            cw.write(pack(command(1, browse_bridge.CONNECTIONS_METHOD, {})))
            await cw.drain()
            reply = await read_frame(cr, 1 << 20)
            cw.close()
            return [row["connectionId"] for row in reply["result"]["connections"]]
        self.assertEqual(self.run_bridge(body), [])


async def _pair_streams() -> tuple[tuple, tuple]:
    """一对真实 asyncio 流（socketpair 两端），测试扮演对端。"""
    a, b = socket.socketpair()
    for s in (a, b):
        s.setblocking(False)
    end_a = await asyncio.open_connection(sock=a, limit=browse_ws.MAX_HANDSHAKE_BYTES)
    end_b = await asyncio.open_connection(sock=b, limit=browse_ws.MAX_HANDSHAKE_BYTES)
    return end_a, end_b


class _ExplodingDrain:
    """sock_writer 替身：write 正常，drain 抛错（backpressure 场景）。"""

    def __init__(self, inner) -> None:
        self._inner = inner

    def write(self, data: bytes) -> None:
        self._inner.write(data)

    async def drain(self) -> None:
        raise ConnectionError("simulated backpressure")


class AdapterCase(unittest.TestCase):
    """_Adapter 单元面：e2e 够不着的分支（帧边界、单向泵失败、重复 hello、drain 抛错）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        log_path = pathlib.Path(self._tmp.name) / "bridge.log"
        patch = unittest.mock.patch.dict(os.environ, {"BROWSE_BRIDGE_LOG": str(log_path),
                                               "SCRIPTS_LOG": ""})
        patch.start()
        self.addCleanup(patch.stop)

    def run_adapter(self, body, **adapter_kwargs) -> object:
        """搭 _Adapter + 两对 socketpair（ws 侧 / daemon 侧），测试各扮一端。"""

        async def scenario():
            ws_peer, ws_ad = await _pair_streams()
            sock_peer, sock_ad = await _pair_streams()
            meta: dict[int, dict] = {}
            adapter = browse_bridge._Adapter(
                ws_reader=ws_ad[0], ws_writer=ws_ad[1],
                sock_reader=sock_ad[0], sock_writer=sock_ad[1],
                meta=meta, **adapter_kwargs)
            task = asyncio.create_task(adapter.pump())
            try:
                return await body(meta, task, ws_peer, sock_peer)
            finally:
                for writer in (ws_peer[1], ws_ad[1], sock_peer[1], sock_ad[1]):
                    writer.close()
                if not task.done():
                    task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        return asyncio.run(scenario())

    @staticmethod
    def _hello() -> str:
        return json.dumps({"type": "hello", "role": "extension", "browser": "chrome"})

    def test_frame_exactly_at_limit_passes_and_one_over_is_rejected(self) -> None:
        """上限边界：恰好等于上限的帧通过，多 1 字节被拒（off-by-one 就在这里现形）。"""
        exact = ('{"x":"' + "a" * 56 + '"}').encode()  # 恰 64 字节
        over = ('{"x":"' + "a" * 57 + '"}').encode()   # 65 字节
        self.assertEqual(len(exact), 64)
        self.assertEqual(len(over), 65)

        async def body(meta, task, ws_peer, sock_peer):
            pr, pw = ws_peer
            dr, dw = sock_peer
            with unittest.mock.patch.object(browse_bridge, "MAX_INCOMING_FRAME_BYTES", 64):
                await browse_ws.client_send(pw, exact.decode())
                got = await read_frame(dr, 1 << 20)
                await browse_ws.client_send(pw, over.decode())
                try:
                    await asyncio.wait_for(task, 2.0)
                    exc: BaseException | None = None
                except Exception as e:  # pump 应带着 ConnectionError 结束
                    exc = e
            dw.close()
            return got, exc
        got, exc = self.run_adapter(body)
        self.assertEqual(got["x"], "a" * 56)
        self.assertIsInstance(exc, ConnectionError)

    def test_sock_side_failure_terminates_both_pumps_and_cleans_meta(self) -> None:
        """daemon 侧断流：单向泵失败要把整条适配泵带走，meta 不残留。"""

        async def body(meta, task, ws_peer, sock_peer):
            pr, pw = ws_peer
            dr, dw = sock_peer
            await browse_ws.client_send(pw, self._hello())
            await read_frame(dr, 1 << 20)  # hello 已到 daemon 侧
            dw.write(pack({"type": "hello-ack", "connectionId": 7, "browser": "chrome"}))
            await dw.drain()
            ack = json.loads(await browse_ws.read_message(pr, pw, 1 << 20))
            assert ack["type"] == "hello-ack", ack
            self.assertIn(7, meta)
            dw.close()  # daemon 侧异常断流 → read_frame 抛 IncompleteReadError
            try:
                await asyncio.wait_for(task, 2.0)
                exc: BaseException | None = None
            except Exception as e:
                exc = e
            # 在途消息必丢：泵已死，daemon 侧读到 EOF
            await browse_ws.client_send(pw, '{"id": 9}')
            eof = await dr.read()
            return exc, len(meta), eof
        exc, meta_len, eof = self.run_adapter(body)
        self.assertIsNotNone(exc, "daemon 侧断流没有终止泵")
        self.assertEqual(meta_len, 0, "连接表残留了已断开的连接")
        self.assertEqual(eof, b"", "daemon 侧没有看到 EOF")

    def test_second_hello_is_not_role_translated_again(self) -> None:
        """role 翻译只发生在第一条 hello：第二条原样放行。"""

        async def body(meta, task, ws_peer, sock_peer):
            pr, pw = ws_peer
            dr, dw = sock_peer
            await browse_ws.client_send(pw, self._hello())
            first = await read_frame(dr, 1 << 20)
            await browse_ws.client_send(pw, self._hello())
            second = await read_frame(dr, 1 << 20)
            pw.close(); dw.close()
            return first["role"], second["role"]
        first, second = self.run_adapter(body)
        self.assertEqual(first, "native-host")
        self.assertEqual(second, "extension")

    def test_drain_failure_terminates_the_adapter(self) -> None:
        """backpressure（drain 抛 ConnectionError）：异常要冒出泵，不能被吞。"""

        async def scenario():
            ws_peer, ws_ad = await _pair_streams()
            sock_peer, sock_ad = await _pair_streams()
            meta: dict[int, dict] = {}
            adapter = browse_bridge._Adapter(
                ws_reader=ws_ad[0], ws_writer=ws_ad[1],
                sock_reader=sock_ad[0], sock_writer=_ExplodingDrain(sock_ad[1]),
                meta=meta)
            task = asyncio.create_task(adapter.pump())
            pr, pw = ws_peer
            await browse_ws.client_send(pw, '{"type": "hello", "role": "extension", "browser": "chrome"}')
            try:
                await asyncio.wait_for(task, 2.0)
                exc: BaseException | None = None
            except Exception as e:
                exc = e
            for writer in (pw, ws_ad[1], sock_peer[1], sock_ad[1]):
                writer.close()
            return exc, len(meta)
        exc, meta_len = asyncio.run(scenario())
        self.assertIsInstance(exc, ConnectionError)
        self.assertIn("backpressure", str(exc))
        self.assertEqual(meta_len, 0)

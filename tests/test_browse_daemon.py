"""browse daemon 测试：socket 权限、幂等启动、握手分流、多路复用、订阅重建、空闲退出。

权限位一律用 `os.stat` 实测，不靠读代码判断。
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import shutil
import stat
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.browse_daemon import (  # noqa: E402
    ABORT_METHOD,
    BROWSERS_METHOD,
    MAX_HELLO_BYTES,
    ROLE_CLI,
    ROLE_NATIVE_HOST,
    UNKNOWN_BROWSER,
    Daemon,
    _Conn,
    bind,
    connect,
    pack,
    probe,
    read_frame,
    run,
    socket_path,
)
from lib.browse_protocol import (  # noqa: E402
    ERR_ABORTED,
    ERR_INVALID_ARGUMENT,
    ERR_NOT_CONNECTED,
    ERR_UNKNOWN_COMMAND,
    MAX_INCOMING_FRAME_BYTES,
    ProtocolError,
    command,
    error,
    event,
    success,
)

TIMEOUT = 2.0


async def recv(reader: asyncio.StreamReader, timeout: float = TIMEOUT) -> dict:
    return await asyncio.wait_for(read_frame(reader, MAX_INCOMING_FRAME_BYTES), timeout)


async def send(writer: asyncio.StreamWriter, message: dict) -> None:
    writer.write(pack(message))
    await writer.drain()


async def at_eof(reader: asyncio.StreamReader, timeout: float = TIMEOUT) -> bool:
    """对端有没有把连接断掉。"""
    try:
        return await asyncio.wait_for(reader.read(1), timeout) == b""
    except asyncio.TimeoutError:
        return False


def mode_of(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


class TestSocketPath(unittest.TestCase):
    def test_prefers_xdg_runtime_dir(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/501"}):
            self.assertEqual(socket_path(), pathlib.Path("/run/user/501/lazygophers/browse.sock"))

    def test_falls_back_to_user_state_dir(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                socket_path(),
                pathlib.Path.home() / ".local" / "state" / "lazygophers" / "scripts" / "browse.sock",
            )


class TestPermissions(unittest.TestCase):
    """鉴权就是文件权限，所以这几条必须实测 os.stat。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = pathlib.Path(self.tmp) / "lazygophers" / "browse.sock"

    def test_socket_is_0600_and_parent_is_0700_at_birth(self):
        sock = bind(self.path)
        self.addCleanup(sock.close)
        self.assertEqual(mode_of(self.path), 0o600)
        self.assertEqual(mode_of(self.path.parent), 0o700)

    def test_tightens_a_preexisting_loose_parent_dir(self):
        self.path.parent.mkdir(parents=True)
        os.chmod(self.path.parent, 0o777)
        sock = bind(self.path)
        self.addCleanup(sock.close)
        self.assertEqual(mode_of(self.path.parent), 0o700)

    def test_permissive_umask_does_not_loosen_the_socket(self):
        """就算调用方的 umask 是 0，socket 也必须是 0600——不能先松后紧。"""
        old = os.umask(0o000)
        try:
            sock = bind(self.path)
            self.addCleanup(sock.close)
            self.assertEqual(mode_of(self.path), 0o600)
            self.assertEqual(os.umask(0o000), 0o000, "umask 必须被原样还回去")
        finally:
            os.umask(old)


class TestProbe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = pathlib.Path(self.tmp) / "browse.sock"

    def test_missing_path_is_not_running(self):
        self.assertFalse(probe(self.path))

    def test_plain_file_is_not_running(self):
        self.path.write_text("not a socket")
        self.assertFalse(probe(self.path))

    def test_bound_but_unlistened_socket_is_not_running(self):
        """bind 了却没 listen 的死 socket：连不上就是死的。"""
        sock = bind(self.path)
        self.addCleanup(sock.close)
        self.assertFalse(probe(self.path))

    def test_listening_socket_is_running(self):
        sock = bind(self.path)
        self.addCleanup(sock.close)
        sock.listen(1)
        self.assertTrue(probe(self.path))


class DaemonCase(unittest.IsolatedAsyncioTestCase):
    idle_timeout = 300.0

    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = pathlib.Path(self.tmp) / "lazygophers" / "browse.sock"
        # daemon 现在是纯管道：没有配置要读，也没有审计要写
        self.daemon = Daemon(path=self.path, idle_timeout=self.idle_timeout)
        self.assertTrue(await self.daemon.start())
        self._writers = []

    async def asyncTearDown(self):
        for writer in self._writers:
            writer.close()
        await self.daemon.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def client(self, role: str, browser: str = ""):
        reader, writer, conn_id = await connect(role, self.path, browser=browser)
        self._writers.append(writer)
        return reader, writer, conn_id

    async def settle(self):
        """给 daemon 一个调度间隙把 attach / detach 走完。"""
        await asyncio.sleep(0.05)


class TestStartup(DaemonCase):
    async def test_running_daemon_socket_is_0600(self):
        self.assertEqual(mode_of(self.path), 0o600)
        self.assertEqual(mode_of(self.path.parent), 0o700)

    async def test_second_start_is_a_no_op(self):
        other = Daemon(path=self.path)
        self.assertFalse(await other.start())
        self.assertTrue(probe(self.path), "幂等启动不能把在跑那个的 socket 拆了")

    async def test_dead_socket_file_is_replaced(self):
        await self.daemon.stop()
        self.path.write_text("leftover from a crash")
        self.assertTrue(await self.daemon.start())
        self.assertTrue(probe(self.path))

    async def test_stop_removes_the_socket(self):
        await self.daemon.stop()
        self.assertFalse(self.path.exists())

    async def test_run_returns_false_when_one_is_already_up(self):
        self.assertFalse(await run(self.path))


class TestHandshake(DaemonCase):
    async def test_ack_carries_an_increasing_connection_id(self):
        _, _, first = await self.client(ROLE_CLI)
        _, _, second = await self.client(ROLE_NATIVE_HOST)
        self.assertGreater(second, first)

    async def test_unknown_role_is_rejected_and_closed(self):
        reader, writer = await asyncio.open_unix_connection(str(self.path))
        self._writers.append(writer)
        await send(writer, {"type": "hello", "role": "admin"})
        reply = await recv(reader)
        self.assertEqual(reply["error"], ERR_INVALID_ARGUMENT)
        self.assertTrue(await at_eof(reader))

    async def test_first_frame_must_be_hello(self):
        reader, writer = await asyncio.open_unix_connection(str(self.path))
        self._writers.append(writer)
        await send(writer, command(1, "browsingContext.navigate"))
        reply = await recv(reader)
        self.assertEqual(reply["error"], ERR_INVALID_ARGUMENT)
        self.assertTrue(await at_eof(reader))

    async def test_oversized_hello_frame_is_dropped(self):
        reader, writer = await asyncio.open_unix_connection(str(self.path))
        self._writers.append(writer)
        writer.write(struct.pack("<I", MAX_HELLO_BYTES + 1))
        await writer.drain()
        self.assertTrue(await at_eof(reader))

    async def test_connect_rejects_an_unknown_role_locally(self):
        with self.assertRaises(ProtocolError):
            await connect("root", self.path)


class TestBrowserNotConnected(DaemonCase):
    async def test_command_fails_immediately_without_a_browser(self):
        reader, writer, _ = await self.client(ROLE_CLI)
        await send(writer, command(1, "browsingContext.navigate", {"url": "https://example.com"}))
        reply = await recv(reader)
        self.assertEqual(reply, {"type": "error", "id": 1, "error": ERR_NOT_CONNECTED,
                                 "message": reply["message"]})
        self.assertIn("浏览器未连接", reply["message"])

    async def test_it_does_not_queue_for_a_later_browser(self):
        """先失败，后连上的浏览器不该收到那条指令——排队等于悄悄延迟执行副作用。"""
        cli_reader, cli_writer, _ = await self.client(ROLE_CLI)
        await send(cli_writer, command(1, "browsingContext.navigate", {"url": "https://a.com"}))
        self.assertEqual((await recv(cli_reader))["error"], ERR_NOT_CONNECTED)
        browser_reader, _, _ = await self.client(ROLE_NATIVE_HOST)
        with self.assertRaises(asyncio.TimeoutError):
            await recv(browser_reader, timeout=0.3)


class TestMultiplexing(DaemonCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.browser_reader, self.browser_writer, self.browser_id = await self.client(ROLE_NATIVE_HOST)
        await self.settle()

    async def test_command_gets_a_daemon_id_and_the_answer_comes_back_with_the_cli_id(self):
        cli_reader, cli_writer, _ = await self.client(ROLE_CLI)
        await send(cli_writer, command(7, "script.evaluate", {"expression": "1+1"}))
        forwarded = await recv(self.browser_reader)
        self.assertEqual(forwarded["method"], "script.evaluate")
        self.assertEqual(forwarded["params"], {"expression": "1+1"})
        self.assertNotIn("type", forwarded)

        await send(self.browser_writer, success(forwarded["id"], {"value": 2}))
        self.assertEqual(await recv(cli_reader), success(7, {"value": 2}))

    async def test_two_clis_using_the_same_id_do_not_cross(self):
        a_reader, a_writer, _ = await self.client(ROLE_CLI)
        b_reader, b_writer, _ = await self.client(ROLE_CLI)
        await send(a_writer, command(1, "script.evaluate", {"expression": "'a'"}))
        await send(b_writer, command(1, "script.evaluate", {"expression": "'b'"}))

        first, second = await recv(self.browser_reader), await recv(self.browser_reader)
        self.assertNotEqual(first["id"], second["id"], "daemon 必须重新分配全局唯一 id")
        by_expr = {m["params"]["expression"]: m["id"] for m in (first, second)}

        # 故意反着回，验证是按 id 回收而不是按到达顺序
        await send(self.browser_writer, success(by_expr["'b'"], {"who": "b"}))
        await send(self.browser_writer, success(by_expr["'a'"], {"who": "a"}))
        self.assertEqual(await recv(a_reader), success(1, {"who": "a"}))
        self.assertEqual(await recv(b_reader), success(1, {"who": "b"}))

    async def test_error_replies_are_routed_too(self):
        cli_reader, cli_writer, _ = await self.client(ROLE_CLI)
        await send(cli_writer, command(3, "input.click"))
        forwarded = await recv(self.browser_reader)
        await send(self.browser_writer, error(forwarded["id"], "no such element", "css=nope"))
        self.assertEqual(await recv(cli_reader), error(3, "no such element", "css=nope"))

    async def test_reply_to_an_unknown_id_is_dropped(self):
        cli_reader, _, _ = await self.client(ROLE_CLI)
        await send(self.browser_writer, success(99999, {"stolen": True}))
        with self.assertRaises(asyncio.TimeoutError):
            await recv(cli_reader, timeout=0.3)

    async def test_the_connection_id_guard_drops_a_foreign_answer(self):
        """connectionId 校验本身：pending 还在，但回包来自另一条连接 —— 丢掉。

        直接喂 `_from_browser`，因为正常路径下浏览器被顶替时在途指令已经先失败了，
        这道防线要单独verify它拦得住，而不是靠上游碰巧没让它触发。
        """
        cli_reader, cli_writer, _ = await self.client(ROLE_CLI)
        await send(cli_writer, command(5, "script.evaluate"))
        gid = (await recv(self.browser_reader))["id"]

        imposter = _Conn(id=self.browser_id + 999, role=ROLE_NATIVE_HOST, writer=self.browser_writer)
        await self.daemon._from_browser(imposter, success(gid, {"value": "hijacked"}))
        with self.assertRaises(asyncio.TimeoutError):
            await recv(cli_reader, timeout=0.3)

        # 真连接回的包照常送达 —— 刚才丢掉的是冒名者，不是整条指令
        await send(self.browser_writer, success(gid, {"value": 2}))
        self.assertEqual(await recv(cli_reader), success(5, {"value": 2}))

    async def test_replacing_the_browser_fails_the_earlier_connections_commands(self):
        """新 native host 顶掉旧的：旧连接的在途指令当场失败，不等一个回不来的包。"""
        cli_reader, cli_writer, _ = await self.client(ROLE_CLI)
        await send(cli_writer, command(5, "script.evaluate", {"expression": "secret"}))
        forwarded = await recv(self.browser_reader)

        _, hijacker_writer, hijacker_id = await self.client(ROLE_NATIVE_HOST)
        self.assertNotEqual(hijacker_id, self.browser_id)
        await self.settle()
        await send(hijacker_writer, success(forwarded["id"], {"value": "hijacked"}))

        reply = await recv(cli_reader)
        self.assertEqual(reply["type"], "error", "抢答的 success 绝不能顶替掉真回包")
        self.assertEqual(reply["error"], ERR_NOT_CONNECTED)
        with self.assertRaises(asyncio.TimeoutError):
            await recv(cli_reader, timeout=0.3)

    async def test_browser_disconnect_fails_every_inflight_command(self):
        cli_reader, cli_writer, _ = await self.client(ROLE_CLI)
        await send(cli_writer, command(1, "script.evaluate"))
        await send(cli_writer, command(2, "script.evaluate"))
        await recv(self.browser_reader)
        await recv(self.browser_reader)

        self.browser_writer.close()
        replies = [await recv(cli_reader), await recv(cli_reader)]
        self.assertEqual(sorted(r["id"] for r in replies), [1, 2])
        self.assertTrue(all(r["error"] == ERR_NOT_CONNECTED for r in replies))

    async def test_browser_does_not_get_to_send_commands(self):
        """浏览器那头只回包和推事件；发来的 Command 一律忽略，不广播给 CLI。"""
        cli_reader, _, _ = await self.client(ROLE_CLI)
        await self.settle()
        await send(self.browser_writer, command(1, "script.evaluate", {"expression": "evil"}))
        with self.assertRaises(asyncio.TimeoutError):
            await recv(cli_reader, timeout=0.3)

    async def test_cli_disconnect_drops_its_inflight_commands(self):
        """CLI 走了，它的在途指令就没人收了；回包晚到也不该让 daemon 出事。"""
        _, cli_writer, _ = await self.client(ROLE_CLI)
        await send(cli_writer, command(1, "script.evaluate"))
        forwarded = await recv(self.browser_reader)
        cli_writer.close()
        await self.settle()
        await send(self.browser_writer, success(forwarded["id"], {"value": 1}))
        await self.settle()

        # daemon 还活着：新 CLI 照常能用
        new_reader, new_writer, _ = await self.client(ROLE_CLI)
        await send(new_writer, command(2, "script.evaluate"))
        again = await recv(self.browser_reader)
        await send(self.browser_writer, success(again["id"], {"value": 2}))
        self.assertEqual(await recv(new_reader), success(2, {"value": 2}))

    async def test_cli_only_sends_commands(self):
        """CLI 发来的 Success/Event 一概忽略，不当成指令转给浏览器。"""
        _, cli_writer, _ = await self.client(ROLE_CLI)
        await send(cli_writer, success(1, {"nope": True}))
        await send(cli_writer, event("log.entryAdded"))
        with self.assertRaises(asyncio.TimeoutError):
            await recv(self.browser_reader, timeout=0.3)


class TestEvents(DaemonCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.browser_reader, self.browser_writer, _ = await self.client(ROLE_NATIVE_HOST)
        await self.settle()

    async def test_unsubscribed_events_reach_every_cli(self):
        a_reader, _, _ = await self.client(ROLE_CLI)
        b_reader, _, _ = await self.client(ROLE_CLI)
        await self.settle()
        entry = event("log.entryAdded", {"level": "warn"})
        await send(self.browser_writer, entry)
        self.assertEqual(await recv(a_reader), entry)
        self.assertEqual(await recv(b_reader), entry)

    async def test_keepalive_is_swallowed(self):
        cli_reader, _, _ = await self.client(ROLE_CLI)
        await self.settle()
        await send(self.browser_writer, event("lg:keepalive.ping"))
        with self.assertRaises(asyncio.TimeoutError):
            await recv(cli_reader, timeout=0.3)


class TestSubscriptions(DaemonCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.browser_reader, self.browser_writer, _ = await self.client(ROLE_NATIVE_HOST)
        await self.settle()
        self.cli_reader, self.cli_writer, _ = await self.client(ROLE_CLI)

    async def subscribe(self, cli_id: int, ext_id: str, params: dict | None = None) -> dict:
        """替 CLI 走一遍 network.subscribe，返回它拿到的 Success。"""
        await send(self.cli_writer, command(cli_id, "network.subscribe", params or {"matchUrl": "*/api/*"}))
        forwarded = await recv(self.browser_reader)
        await send(self.browser_writer,
                   success(forwarded["id"], {"subscription": ext_id, "lg:metadataOnly": True}))
        return await recv(self.cli_reader)

    async def test_cli_gets_a_daemon_owned_subscription_id(self):
        reply = await self.subscribe(1, "net-1")
        self.assertEqual(reply["id"], 1)
        self.assertEqual(reply["result"]["subscription"], "sub-1")
        self.assertTrue(reply["result"]["lg:metadataOnly"])

    async def test_events_carry_the_daemon_id_and_only_reach_the_owner(self):
        await self.subscribe(1, "net-1")
        other_reader, _, _ = await self.client(ROLE_CLI)
        await self.settle()

        await send(self.browser_writer, event("network.responseCompleted", {
            "subscriptions": ["net-1"], "response": {"status": 200}}))
        got = await recv(self.cli_reader)
        self.assertEqual(got["params"]["subscriptions"], ["sub-1"])
        with self.assertRaises(asyncio.TimeoutError):
            await recv(other_reader, timeout=0.3)

    async def test_event_for_an_unknown_subscription_goes_nowhere(self):
        await self.subscribe(1, "net-1")
        await send(self.browser_writer,
                   event("network.responseCompleted", {"subscriptions": ["net-404"]}))
        with self.assertRaises(asyncio.TimeoutError):
            await recv(self.cli_reader, timeout=0.3)

    async def test_unsubscribe_translates_both_directions(self):
        await self.subscribe(1, "net-1")
        await send(self.cli_writer, command(2, "network.unsubscribe", {"subscription": "sub-1"}))
        forwarded = await recv(self.browser_reader)
        self.assertEqual(forwarded["params"], {"subscription": "net-1"})
        await send(self.browser_writer, success(forwarded["id"], {"removed": ["net-1"]}))
        reply = await recv(self.cli_reader)
        self.assertEqual(reply["result"]["removed"], ["sub-1"])

        # 退订之后事件不该再送过来
        await send(self.browser_writer, event("network.responseCompleted", {"subscriptions": ["net-1"]}))
        with self.assertRaises(asyncio.TimeoutError):
            await recv(self.cli_reader, timeout=0.3)

    async def test_unknown_subscription_id_is_passed_through_untouched(self):
        await send(self.cli_writer, command(2, "network.unsubscribe", {"subscription": "sub-nope"}))
        forwarded = await recv(self.browser_reader)
        self.assertEqual(forwarded["params"], {"subscription": "sub-nope"})

    async def test_rebuilt_after_the_extension_reloads(self):
        """扩展的订阅活在 service worker 内存里，重载必然清零 —— daemon 得补回来。"""
        await self.subscribe(1, "net-1", {"matchUrl": "*/api/orders*"})
        self.browser_writer.close()
        await self.settle()

        new_reader, new_writer, _ = await self.client(ROLE_NATIVE_HOST)
        rebuilt = await recv(new_reader)
        self.assertEqual(rebuilt["method"], "network.subscribe")
        self.assertEqual(rebuilt["params"], {"matchUrl": "*/api/orders*"})
        await send(new_writer, success(rebuilt["id"], {"subscription": "net-77"}))
        await self.settle()

        # 重建用的是扩展这一世的新 id，但 CLI 看到的还是它当初拿到的那个
        await send(new_writer, event("network.responseCompleted", {"subscriptions": ["net-77"]}))
        got = await recv(self.cli_reader)
        self.assertEqual(got["params"]["subscriptions"], ["sub-1"])

    async def test_the_rebuild_result_is_not_forwarded_to_the_cli(self):
        await self.subscribe(1, "net-1")
        self.browser_writer.close()
        await self.settle()
        new_reader, new_writer, _ = await self.client(ROLE_NATIVE_HOST)
        rebuilt = await recv(new_reader)
        await send(new_writer, success(rebuilt["id"], {"subscription": "net-77"}))
        with self.assertRaises(asyncio.TimeoutError):
            await recv(self.cli_reader, timeout=0.3)

    async def test_a_failed_rebuild_drops_the_subscription(self):
        await self.subscribe(1, "net-1")
        self.browser_writer.close()
        await self.settle()
        new_reader, new_writer, _ = await self.client(ROLE_NATIVE_HOST)
        rebuilt = await recv(new_reader)
        await send(new_writer, error(rebuilt["id"], ERR_INVALID_ARGUMENT, "webRequest 权限没了"))
        await self.settle()
        await send(new_writer, event("network.responseCompleted", {"subscriptions": ["net-1"]}))
        with self.assertRaises(asyncio.TimeoutError):
            await recv(self.cli_reader, timeout=0.3)

    async def test_cli_disconnect_releases_its_subscription_on_the_extension(self):
        await self.subscribe(1, "net-1")
        self.cli_writer.close()
        released = await recv(self.browser_reader)
        self.assertEqual(released["method"], "network.unsubscribe")
        self.assertEqual(released["params"], {"subscription": "net-1"})


class TestHostileClient(DaemonCase):
    async def test_a_malformed_frame_disconnects_instead_of_resyncing(self):
        """坏帧之后缓冲已经错位，续读只会一路错下去。"""
        reader, writer, _ = await self.client(ROLE_CLI)
        body = b"{not json}"
        writer.write(struct.pack("<I", len(body)) + body)
        await writer.drain()
        self.assertTrue(await at_eof(reader))

    async def test_a_never_ending_frame_hits_the_buffer_ceiling(self):
        """单帧上限管不着「帧永远发不完」，缓冲上限管得着。"""
        reader, writer, _ = await self.client(ROLE_CLI)
        with mock.patch("lib.browse_daemon.MAX_BUFFER_BYTES", 4096):
            writer.write(struct.pack("<I", 10 * 1024 * 1024))
            writer.write(b"x" * 8192)
            await writer.drain()
            self.assertTrue(await at_eof(reader))

    async def test_a_frame_declaring_more_than_the_protocol_cap_disconnects(self):
        reader, writer, _ = await self.client(ROLE_CLI)
        writer.write(struct.pack("<I", MAX_INCOMING_FRAME_BYTES + 1))
        await writer.drain()
        self.assertTrue(await at_eof(reader))


class TestIdleExit(DaemonCase):
    idle_timeout = 0.2

    async def test_exits_when_idle_with_nobody_connected(self):
        await asyncio.wait_for(self.daemon.wait_stopped(), timeout=TIMEOUT)

    async def test_a_connected_client_keeps_it_alive(self):
        await self.client(ROLE_CLI)
        await self.settle()
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.daemon.wait_stopped(), timeout=0.6)

    async def test_the_clock_restarts_on_every_command(self):
        reader, writer, _ = await self.client(ROLE_CLI)
        # sleep 放在发指令**之前**：最后一条指令后面不留空档，否则「指令刚停」那一
        # 段就只剩 0.2-0.1=0.1 秒余量，机器一忙就翻车
        for _ in range(4):
            await asyncio.sleep(0.1)
            await send(writer, command(1, "script.evaluate"))
            self.assertEqual((await recv(reader))["error"], ERR_NOT_CONNECTED)
        writer.close()
        # 总共睡了 0.4 秒 > idle_timeout，没退就说明每条指令都把表拨回去了
        # 指令刚停，还不到 idle_timeout，不能退
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.daemon.wait_stopped(), timeout=0.1)
        await asyncio.wait_for(self.daemon.wait_stopped(), timeout=TIMEOUT)


class TestRun(unittest.IsolatedAsyncioTestCase):
    async def test_run_serves_until_it_goes_idle_and_cleans_up(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = pathlib.Path(tmp) / "lazygophers" / "browse.sock"
        self.assertTrue(await asyncio.wait_for(run(path, idle_timeout=0.2), timeout=TIMEOUT))
        self.assertFalse(path.exists(), "退出时要把 socket 收掉，不留死 socket 给下一次")

    async def test_connect_refuses_a_server_that_does_not_ack(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = pathlib.Path(tmp) / "impostor.sock"

        async def impostor(reader, writer):
            await read_frame(reader, MAX_HELLO_BYTES)
            writer.write(pack({"type": "go away"}))
            await writer.drain()

        server = await asyncio.start_unix_server(impostor, sock=bind(path))
        self.addCleanup(server.close)
        with self.assertRaises(ProtocolError):
            await connect(ROLE_CLI, path)


class TestPackAndReadFrame(unittest.IsolatedAsyncioTestCase):
    def test_pack_is_little_endian_and_respects_the_limit(self):
        raw = pack({"type": "hello", "role": ROLE_CLI})
        self.assertEqual(struct.unpack("<I", raw[:4])[0], len(raw) - 4)
        with self.assertRaises(ProtocolError):
            pack({"blob": "x" * 100}, limit=10)

    def test_pack_allows_payloads_the_browser_direction_would_reject(self):
        """daemon → CLI 是本机 socket，截图必然超 1 MB，不能按 1 MB 卡。"""
        big = {"type": "success", "id": 1, "result": {"png": "x" * (2 * 1024 * 1024)}}
        self.assertGreater(len(pack(big)), 2 * 1024 * 1024)

    async def test_read_frame_rejects_a_zero_length_and_a_non_object_body(self):
        for payload in (b"", b'"just a string"'):
            reader = asyncio.StreamReader()
            reader.feed_data(struct.pack("<I", len(payload)) + payload)
            reader.feed_eof()
            with self.assertRaises(ProtocolError):
                await read_frame(reader, MAX_HELLO_BYTES)

    async def test_read_frame_rejects_bad_utf8(self):
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack("<I", 2) + b"\xff\xfe")
        reader.feed_eof()
        with self.assertRaises(ProtocolError):
            await read_frame(reader, MAX_HELLO_BYTES)


# ---------------------------------------------------------------- 安全接线（T11）

# 高危指令（`RISKY_METHODS`），且 params 里带得出域名 —— 确认框上要写清楚是哪个站
COOKIES = command(1, "storage.getCookies", {"domain": "bank.test"})
# 非高危：任何模式下都不该弹确认
NAV = command(1, "browsingContext.navigate", {"url": "https://bank.test/x"})


class WiringCase(DaemonCase):
    """CLI ──▶ daemon ──▶ 扩展 的端到端脚手架。

    `self.browser_*` 就是 stub 扩展。确认那一段没有了 —— daemon 不再问任何问题，
    弹不弹框是插件自己的事（`browser-extension/extension/src/policy.ts`）。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.browser_reader, self.browser_writer, _ = await self.client(ROLE_NATIVE_HOST)
        self.cli_reader, self.cli_writer, _ = await self.client(ROLE_CLI)
        await self.settle()



class TestAbort(WiringCase):
    """`browse stop`：在途指令全掐，daemon 留着（spec 4.5）。"""

    security_cfg = {"confirm_mode": "silent"}

    async def test_inflight_commands_fail_and_the_daemon_stays_up(self):
        await send(self.cli_writer, COOKIES)
        await recv(self.browser_reader)  # 已经发给浏览器，故意不回

        other_reader, other_writer, _ = await self.client(ROLE_CLI)
        await self.settle()
        await send(other_writer, command(9, ABORT_METHOD))
        self.assertEqual(await recv(other_reader), success(9, {"aborted": 1}))

        reply = await recv(self.cli_reader)
        self.assertEqual((reply["id"], reply["error"]), (1, ERR_ABORTED))
        self.assertTrue(probe(self.path), "daemon 不该跟着退")

    async def test_aborting_with_nothing_inflight_is_fine(self):
        await send(self.cli_writer, command(9, ABORT_METHOD))
        self.assertEqual(await recv(self.cli_reader), success(9, {"aborted": 0}))

    async def test_abort_works_without_a_browser(self):
        self.browser_writer.close()
        await self.settle()
        await send(self.cli_writer, command(9, ABORT_METHOD))
        self.assertEqual((await recv(self.cli_reader))["type"], "success")


class DeadWriter:
    """一个写就炸的 writer：模拟「刚决定要发，连接就没了」那一瞬间。"""

    def write(self, _data):
        raise ConnectionError("对端没了")

    async def drain(self):
        pass


class TestFailClosed(DaemonCase):
    """连接在最不巧的那一刻断掉时，每条路都必须倒向失败，不能倒向静默成功。

    2026-09-14 之前这里还测「确认往返发不出去算拒绝」；确认搬进插件之后 daemon 不再
    发起任何问答，剩下的就是转发这一条路。
    """

    async def test_a_command_that_cannot_be_forwarded_fails(self):
        cli_reader, cli_writer, _ = await self.client(ROLE_CLI)
        await self.settle()
        self.daemon._browser = _Conn(id=99, role=ROLE_NATIVE_HOST, writer=DeadWriter())
        await send(cli_writer, command(1, "browsingContext.getTree"))
        self.assertEqual((await recv(cli_reader))["error"], ERR_NOT_CONNECTED)
        self.assertEqual(self.daemon._pending, {}, "发不出去就不能留下一条永远不回的 pending")

    async def test_the_browser_cannot_ask_the_daemon_anything_any_more(self):
        """插件反过来问 daemon 的那三类消息（免确认名单、配置）已经不存在了。

        真收到一条的话必须当场回 unknown command —— 静默吞掉会让插件那边挂着等。
        """
        browser_reader, browser_writer, _ = await self.client(ROLE_NATIVE_HOST)
        await self.settle()
        await send(browser_writer, command(7, "lg:config.get"))
        reply = await recv(browser_reader)
        self.assertEqual(reply["error"], ERR_UNKNOWN_COMMAND)
        self.assertIn("插件", reply["message"])

    async def test_an_envelope_the_browser_should_never_send_is_dropped(self):
        conn = _Conn(id=1, role=ROLE_NATIVE_HOST, writer=DeadWriter())
        await self.daemon._from_browser(conn, {"type": "hello", "id": 1})


class TestManyBrowsers(DaemonCase):
    """一台机器上同时连着好几个浏览器。

    这是个真缺陷的回归测试：以前 daemon 只有一个浏览器插槽，后连的顶掉先连的，而扩展侧
    `native-port.ts` 的 onDisconnect 会退避重连 —— 两者相乘就是 Chrome 和 Brave 无限
    互踢。而 `browse install` 默认给探测到的每个浏览器都注册，等于主动把现场布好了。
    """

    async def two_browsers(self):
        chrome = await self.client(ROLE_NATIVE_HOST, browser="chrome")
        brave = await self.client(ROLE_NATIVE_HOST, browser="brave")
        await self.settle()
        return chrome, brave

    async def test_two_browsers_stay_connected_neither_is_kicked(self):
        """缺陷本身：两个同时连着，谁都不该被踢掉。"""
        (chrome_r, chrome_w, _), (brave_r, brave_w, _) = await self.two_browsers()
        self.assertEqual(sorted(self.daemon._browsers), ["brave", "chrome"])

        # 两条连接都还活着：各自收得到发给自己的指令
        cli_r, cli_w, _ = await self.client(ROLE_CLI, browser="chrome")
        await send(cli_w, command(1, "browsingContext.getTree"))
        self.assertEqual((await recv(chrome_r))["method"], "browsingContext.getTree")

        cli2_r, cli2_w, _ = await self.client(ROLE_CLI, browser="brave")
        await send(cli2_w, command(1, "browsingContext.getTree"))
        self.assertEqual((await recv(brave_r))["method"], "browsingContext.getTree")

    async def test_the_same_browser_reconnecting_does_replace_its_own_slot(self):
        """同名的新连接顶掉旧的 —— 那是同一个浏览器重连，本来就该换。"""
        first_r, first_w, first_id = await self.client(ROLE_NATIVE_HOST, browser="chrome")
        await self.settle()
        second_r, second_w, second_id = await self.client(ROLE_NATIVE_HOST, browser="chrome")
        await self.settle()
        self.assertEqual(list(self.daemon._browsers), ["chrome"])
        self.assertEqual(self.daemon._browsers["chrome"].id, second_id)

    async def test_ambiguity_is_an_error_that_names_every_browser(self):
        """多个连着又没指定：报错必须把都有谁列出来，并写明怎么指定。"""
        await self.two_browsers()
        cli_r, cli_w, _ = await self.client(ROLE_CLI)  # 没指定 --browser
        await send(cli_w, command(1, "browsingContext.getTree"))
        reply = await recv(cli_r)

        self.assertEqual((reply["type"], reply["error"]), ("error", ERR_INVALID_ARGUMENT))
        self.assertIn("chrome", reply["message"], "错误里要说得出都有谁")
        self.assertIn("brave", reply["message"])
        self.assertIn("--browser", reply["message"], "要写明怎么指定")

    async def test_one_browser_needs_no_flag(self):
        """只有一个连着时不用指定 —— 单浏览器的人不该为这个功能付钱。"""
        browser_r, browser_w, _ = await self.client(ROLE_NATIVE_HOST, browser="chrome")
        cli_r, cli_w, _ = await self.client(ROLE_CLI)
        await self.settle()
        await send(cli_w, command(1, "browsingContext.getTree"))
        self.assertEqual((await recv(browser_r))["method"], "browsingContext.getTree")

    async def test_naming_a_browser_that_is_not_connected_says_who_is(self):
        await self.two_browsers()
        cli_r, cli_w, _ = await self.client(ROLE_CLI, browser="firefox")
        await send(cli_w, command(1, "browsingContext.getTree"))
        reply = await recv(cli_r)
        self.assertEqual(reply["error"], ERR_NOT_CONNECTED)
        self.assertIn("firefox", reply["message"])
        self.assertIn("chrome", reply["message"], "要告诉他连着的是谁")

    async def test_the_old_generic_wrapper_still_works(self):
        """升级前装的 wrapper 不带 --browser，握手里就没有这个字段 —— 不能让 daemon 崩。"""
        browser_r, browser_w, _ = await self.client(ROLE_NATIVE_HOST)  # 无 browser
        cli_r, cli_w, _ = await self.client(ROLE_CLI)
        await self.settle()
        self.assertEqual(list(self.daemon._browsers), [UNKNOWN_BROWSER])
        await send(cli_w, command(1, "browsingContext.getTree"))
        self.assertEqual((await recv(browser_r))["method"], "browsingContext.getTree")

    async def test_daemon_status_lists_who_is_connected(self):
        await self.two_browsers()
        cli_r, cli_w, _ = await self.client(ROLE_CLI)
        await send(cli_w, command(9, BROWSERS_METHOD))
        self.assertEqual(await recv(cli_r), success(9, {"browsers": ["brave", "chrome"]}))


class TestBrowsersDoNotInterfere(DaemonCase):
    """A 断开时 B 的在途指令和订阅都不受影响。这条最容易写错。"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.chrome_r, self.chrome_w, self.chrome_id = await self.client(
            ROLE_NATIVE_HOST, browser="chrome")
        self.brave_r, self.brave_w, self.brave_id = await self.client(
            ROLE_NATIVE_HOST, browser="brave")
        self.cli_r, self.cli_w, _ = await self.client(ROLE_CLI, browser="chrome")
        self.cli2_r, self.cli2_w, _ = await self.client(ROLE_CLI, browser="brave")
        await self.settle()

    async def test_a_dropped_browser_only_fails_its_own_inflight_commands(self):
        # 两边各发一条，都不回，让它们挂在途中
        await send(self.cli_w, command(1, "browsingContext.getTree"))
        await send(self.cli2_w, command(1, "browsingContext.getTree"))
        await recv(self.chrome_r)
        await recv(self.brave_r)
        self.assertEqual(len(self.daemon._pending), 2)

        # chrome 掉线
        self.chrome_w.close()
        await self.settle()

        # chrome 那条当场失败
        reply = await recv(self.cli_r)
        self.assertEqual(reply["error"], ERR_NOT_CONNECTED)
        self.assertIn("chrome", reply["message"])

        # brave 那条**还在途中**，没被牵连；brave 回了之后照常送达
        self.assertEqual([p.browser for p in self.daemon._pending.values()], ["brave"])
        await send(self.brave_w, success(list(self.daemon._pending)[0], {"contexts": []}))
        self.assertEqual(await recv(self.cli2_r), success(1, {"contexts": []}))

    async def test_a_dropped_browser_leaves_the_other_ones_subscriptions_alone(self):
        # 两个浏览器各订一个
        await send(self.cli_w, command(1, "network.subscribe", {"matchUrl": "*"}))
        gid = (await recv(self.chrome_r))["id"]
        await send(self.chrome_w, success(gid, {"subscription": "ext-chrome-1"}))
        await recv(self.cli_r)

        await send(self.cli2_w, command(1, "network.subscribe", {"matchUrl": "*"}))
        gid2 = (await recv(self.brave_r))["id"]
        await send(self.brave_w, success(gid2, {"subscription": "ext-brave-1"}))
        await recv(self.cli2_r)

        self.assertEqual(sorted(s.browser for s in self.daemon._subs.values()),
                         ["brave", "chrome"])

        # chrome 重连：只该重建 chrome 自己那一条
        self.chrome_w.close()
        await self.settle()
        new_r, new_w, _ = await self.client(ROLE_NATIVE_HOST, browser="chrome")
        await self.settle()

        rebuilt = await recv(new_r)
        self.assertEqual(rebuilt["method"], "network.subscribe")
        # brave 那边一个包都不该收到
        with self.assertRaises(asyncio.TimeoutError):
            await recv(self.brave_r, timeout=0.3)
        # 两条订阅都还在，各归各的浏览器
        self.assertEqual(sorted(s.browser for s in self.daemon._subs.values()),
                         ["brave", "chrome"])

    async def test_events_from_one_browser_do_not_borrow_the_others_subscription(self):
        """两个浏览器给出同一个 ext_id 时不能串台。"""
        await send(self.cli_w, command(1, "network.subscribe", {"matchUrl": "*"}))
        gid = (await recv(self.chrome_r))["id"]
        await send(self.chrome_w, success(gid, {"subscription": "sub-collision"}))
        chrome_sub = (await recv(self.cli_r))["result"]["subscription"]

        await send(self.cli2_w, command(1, "network.subscribe", {"matchUrl": "*"}))
        gid2 = (await recv(self.brave_r))["id"]
        # 故意给一模一样的内部 id
        await send(self.brave_w, success(gid2, {"subscription": "sub-collision"}))
        brave_sub = (await recv(self.cli2_r))["result"]["subscription"]
        self.assertNotEqual(chrome_sub, brave_sub)

        # brave 推一个事件：必须带 brave 那条订阅的对外 id，不是 chrome 的
        await send(self.brave_w, {"type": "event", "method": "network.responseCompleted",
                                  "params": {"subscriptions": ["sub-collision"], "url": "x"}})
        event = await recv(self.cli2_r)
        self.assertEqual(event["params"]["subscriptions"], [brave_sub])


if __name__ == "__main__":
    unittest.main()

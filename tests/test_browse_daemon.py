"""browse daemon 测试：socket 权限、幂等启动、握手分流、多路复用、订阅重建、空闲退出。

权限位一律用 `os.stat` 实测，不靠读代码判断。
"""

from __future__ import annotations

import asyncio
import json
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
    APPROVALS_APPROVE,
    APPROVALS_LIST,
    APPROVALS_REVOKE,
    CONFIG_GET,
    CONFIG_SET,
    CONFIRM_METHOD,
    CONTEXT_URL_METHOD,
    MAX_HELLO_BYTES,
    ROLE_CLI,
    ROLE_NATIVE_HOST,
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
    ERR_USER_REJECTED,
    MAX_INCOMING_FRAME_BYTES,
    ProtocolError,
    command,
    error,
    event,
    success,
)
from lib.browse_security import Security  # noqa: E402
from lib.cli import browse as browse_cli  # noqa: E402

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

    #: 子类改这个就换掉整份安全配置（confirm_mode / deny_domains / ...）
    security_cfg: dict = {}

    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = pathlib.Path(self.tmp) / "lazygophers" / "browse.sock"
        # 安全层一律指向临时目录：不许任何一条测试读到、写到用户真实的
        # ~/.config/lazygophers/scripts/browse.yaml 或审计目录
        self.audit_dir = pathlib.Path(self.tmp) / "audit"
        self.security = Security(cfg=dict(self.security_cfg),
                                 config_path=pathlib.Path(self.tmp) / "browse.yaml",
                                 audit_dir=self.audit_dir)
        self.daemon = Daemon(path=self.path, idle_timeout=self.idle_timeout,
                             security=self.security)
        self.assertTrue(await self.daemon.start())
        self._writers = []

    def audit_lines(self) -> list[dict]:
        """按写入顺序读回审计记录。没写过就是空表。"""
        path = self.security.audit_path()
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    async def asyncTearDown(self):
        for writer in self._writers:
            writer.close()
        await self.daemon.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def client(self, role: str, confirm_mode: str = ""):
        reader, writer, conn_id = await connect(role, self.path, confirm_mode=confirm_mode)
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
    """CLI ──▶ daemon ──(确认往返)──▶ 扩展 的端到端脚手架。

    `self.browser_*` 就是 stub 扩展：手工替它回 `{approved: ...}`。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.daemon.confirm_timeout = 1.0
        self.browser_reader, self.browser_writer, _ = await self.client(ROLE_NATIVE_HOST)
        self.cli_reader, self.cli_writer, _ = await self.client(ROLE_CLI)
        await self.settle()

    async def answer_confirm(self, approved: bool) -> dict:
        """收下 daemon 主动发来的 `lg:confirm.request` 并替用户回一个布尔。"""
        ask = await recv(self.browser_reader)
        self.assertEqual(ask["method"], CONFIRM_METHOD)
        await send(self.browser_writer, success(ask["id"], {"approved": approved}))
        return ask

    async def expect_no_confirm(self) -> dict:
        """下一条到达扩展的必须是指令本身，不是确认请求。"""
        forwarded = await recv(self.browser_reader)
        self.assertNotEqual(forwarded["method"], CONFIRM_METHOD, "这个模式下不该弹确认")
        return forwarded


class TestSilentMode(WiringCase):
    """默认模式：高危动作直接执行，不打扰用户，但审计照记。"""

    security_cfg = {"confirm_mode": "silent"}

    async def test_a_risky_command_runs_without_any_confirmation(self):
        await send(self.cli_writer, COOKIES)
        forwarded = await self.expect_no_confirm()
        self.assertEqual(forwarded["method"], "storage.getCookies")
        await send(self.browser_writer, success(forwarded["id"], {"cookies": []}))
        self.assertEqual(await recv(self.cli_reader), success(1, {"cookies": []}))

    async def test_the_audit_records_the_domain_and_never_the_params(self):
        await send(self.cli_writer, command(1, "storage.setCookie", {
            "url": "https://bank.test/", "name": "session", "value": "s3cr3t-token"}))
        forwarded = await self.expect_no_confirm()
        await send(self.browser_writer, success(forwarded["id"], {}))
        await recv(self.cli_reader)

        (line,) = self.audit_lines()
        self.assertEqual(line["method"], "storage.setCookie")
        self.assertEqual(line["domain"], "bank.test")
        self.assertEqual(line["action"], "writeCookies")
        self.assertEqual(line["result"], "success")
        self.assertIsInstance(line["ms"], float)
        # params 一个字都不许进审计文件
        blob = json.dumps(line, ensure_ascii=False)
        for leaked in ("s3cr3t-token", "session", "value"):
            self.assertNotIn(leaked, blob)

    async def test_a_failed_command_is_audited_as_an_error(self):
        await send(self.cli_writer, COOKIES)
        forwarded = await self.expect_no_confirm()
        await send(self.browser_writer, error(forwarded["id"], ERR_INVALID_ARGUMENT, "no scope"))
        self.assertEqual((await recv(self.cli_reader))["error"], ERR_INVALID_ARGUMENT)
        (line,) = self.audit_lines()
        self.assertEqual((line["result"], line["error"]), ("error", "no scope"))


class TestAlwaysMode(WiringCase):
    """每一次高危动作都要问，同意过也不记账。"""

    security_cfg = {"confirm_mode": "always"}

    async def test_every_risky_command_asks_again(self):
        for _ in range(2):
            await send(self.cli_writer, COOKIES)
            ask = await self.answer_confirm(True)
            self.assertEqual(ask["params"],
                             {"action": "readCookies", "method": "storage.getCookies",
                              "url": "bank.test"})
            forwarded = await recv(self.browser_reader)
            await send(self.browser_writer, success(forwarded["id"], {"cookies": []}))
            await recv(self.cli_reader)
        self.assertEqual(self.security.approvals(), [], "always 模式不该把域名记成免确认")

    async def test_a_refusal_comes_back_as_user_rejected_and_never_reaches_the_browser(self):
        await send(self.cli_writer, COOKIES)
        await self.answer_confirm(False)
        reply = await recv(self.cli_reader)
        self.assertEqual((reply["type"], reply["id"], reply["error"]),
                         ("error", 1, ERR_USER_REJECTED))
        # CLI 侧把这个码映射成退出码 4
        self.assertEqual(browse_cli.exit_code_for({"status": "failed", "error": reply["error"]}),
                         browse_cli.EXIT_REJECTED)
        with self.assertRaises(asyncio.TimeoutError):
            await recv(self.browser_reader, timeout=0.3)
        (line,) = self.audit_lines()
        self.assertEqual(line["result"], "denied")
        self.assertIn("readCookies", line["error"])

    async def test_a_harmless_command_is_not_confirmed(self):
        await send(self.cli_writer, NAV)
        forwarded = await self.expect_no_confirm()
        self.assertEqual(forwarded["method"], "browsingContext.navigate")

    async def test_no_answer_before_the_timeout_counts_as_a_refusal(self):
        self.daemon.confirm_timeout = 0.2
        await send(self.cli_writer, COOKIES)
        ask = await recv(self.browser_reader)
        self.assertEqual(ask["method"], CONFIRM_METHOD)  # 收到了，但故意不回
        reply = await recv(self.cli_reader, timeout=2.0)
        self.assertEqual(reply["error"], ERR_USER_REJECTED)
        self.assertEqual(self.audit_lines()[0]["result"], "denied")

    async def test_a_browser_that_drops_mid_dialog_counts_as_a_refusal(self):
        """扩展的 service worker 在弹窗期间被回收：端口断 → 确认拿不回来 → 拒绝。"""
        await send(self.cli_writer, COOKIES)
        await recv(self.browser_reader)
        self.browser_writer.close()
        reply = await recv(self.cli_reader, timeout=2.0)
        self.assertEqual(reply["error"], ERR_USER_REJECTED)


class TestPerDomainMode(WiringCase):
    """问一次就记住，撤销后重新问。"""

    security_cfg = {"confirm_mode": "per_domain"}

    async def run_cookies(self) -> dict:
        """跑一条高危指令，返回扩展实际收到的第一条消息。"""
        await send(self.cli_writer, COOKIES)
        return await recv(self.browser_reader)

    async def test_first_time_asks_second_time_does_not_and_revoke_restores_it(self):
        first = await self.run_cookies()
        self.assertEqual(first["method"], CONFIRM_METHOD)
        await send(self.browser_writer, success(first["id"], {"approved": True}))
        forwarded = await recv(self.browser_reader)
        await send(self.browser_writer, success(forwarded["id"], {"cookies": []}))
        await recv(self.cli_reader)
        self.assertEqual(self.security.approvals(), ["bank.test"], "同意后必须落盘")

        second = await self.run_cookies()
        self.assertEqual(second["method"], "storage.getCookies", "同域名第二次不该再问")
        await send(self.browser_writer, success(second["id"], {"cookies": []}))
        await recv(self.cli_reader)

        self.security.revoke("bank.test")
        third = await self.run_cookies()
        self.assertEqual(third["method"], CONFIRM_METHOD, "撤销后必须恢复问")

    async def test_approval_is_per_domain_not_global(self):
        self.security.approve("bank.test")
        await send(self.cli_writer, command(1, "storage.getCookies", {"domain": "other.test"}))
        ask = await recv(self.browser_reader)
        self.assertEqual(ask["params"]["url"], "other.test")

    async def test_a_refusal_is_not_written_down(self):
        first = await self.run_cookies()
        await send(self.browser_writer, success(first["id"], {"approved": False}))
        self.assertEqual((await recv(self.cli_reader))["error"], ERR_USER_REJECTED)
        self.assertEqual(self.security.approvals(), [])


class TestDenyList(WiringCase):
    """拒绝名单优先级最高：命中就直接拒，一个确认框都不弹。"""

    security_cfg = {"confirm_mode": "always", "deny_domains": ["bank.test"]}

    async def test_a_denied_domain_is_refused_without_asking(self):
        await send(self.cli_writer, COOKIES)
        reply = await recv(self.cli_reader)
        self.assertEqual(reply["error"], ERR_USER_REJECTED)
        self.assertIn("拒绝名单", reply["message"])
        with self.assertRaises(asyncio.TimeoutError):
            await recv(self.browser_reader, timeout=0.3)
        self.assertEqual(self.audit_lines()[0]["result"], "denied")

    async def test_it_also_covers_harmless_commands_and_subdomains(self):
        await send(self.cli_writer, command(1, "browsingContext.navigate",
                                            {"url": "https://login.bank.test/"}))
        self.assertEqual((await recv(self.cli_reader))["error"], ERR_USER_REJECTED)

    async def test_another_domain_still_goes_through_the_normal_flow(self):
        await send(self.cli_writer, command(1, "storage.getCookies", {"domain": "shop.test"}))
        await self.answer_confirm(True)
        self.assertEqual((await recv(self.browser_reader))["method"], "storage.getCookies")


class TestDenyListOnPageCommands(WiringCase):
    """`input.*` / `script.*` 的 params 里没有 url —— 拒绝名单得先问扩展目标页是谁。"""

    security_cfg = {"confirm_mode": "silent", "deny_domains": ["bank.test"]}

    CLICK = command(1, "input.click", {"selector": "text=转账"})

    async def answer_context_url(self, url) -> dict:
        ask = await recv(self.browser_reader)
        self.assertEqual(ask["method"], CONTEXT_URL_METHOD)
        await send(self.browser_writer, success(ask["id"], {"url": url}))
        return ask

    async def test_a_click_on_a_denied_page_is_refused(self):
        await send(self.cli_writer, self.CLICK)
        await self.answer_context_url("https://bank.test/transfer")
        reply = await recv(self.cli_reader)
        self.assertEqual(reply["error"], ERR_USER_REJECTED)
        self.assertIn("拒绝名单", reply["message"])
        # 指令本身一个字节都没发给浏览器
        with self.assertRaises(asyncio.TimeoutError):
            await recv(self.browser_reader, timeout=0.3)
        self.assertEqual(self.audit_lines()[0]["result"], "denied")

    async def test_a_click_on_any_other_page_goes_through(self):
        await send(self.cli_writer, self.CLICK)
        await self.answer_context_url("https://shop.test/cart")
        forwarded = await recv(self.browser_reader)
        self.assertEqual(forwarded["method"], "input.click")

    async def test_an_unanswerable_target_fails_closed(self):
        """问不到目标页就不放行 —— 不知道打在谁身上，就不能打。"""
        await send(self.cli_writer, self.CLICK)
        await self.answer_context_url(None)
        reply = await recv(self.cli_reader)
        self.assertEqual(reply["error"], ERR_USER_REJECTED)
        self.assertIn("deny_domains", reply["message"])
        with self.assertRaises(asyncio.TimeoutError):
            await recv(self.browser_reader, timeout=0.3)

    async def test_the_context_lookup_carries_the_caller_s_target_selection(self):
        await send(self.cli_writer, command(1, "script.evaluate",
                                            {"expression": "1", "context": "7"}))
        ask = await self.answer_context_url("https://shop.test/")
        self.assertEqual(ask["params"], {"context": "7"})


class TestConfirmAsksAboutTheRightPage(WiringCase):
    """要弹确认的页面类指令，先问清楚是哪一页 —— 否则确认框没有主语，而且扩展侧的
    去抖缓存会把 daemon 的 `url=None` 和扩展的真实 URL 当成两个问题，弹两次框。"""

    security_cfg = {"confirm_mode": "always"}

    async def test_the_confirm_request_carries_the_resolved_url(self):
        await send(self.cli_writer, command(1, "input.click", {"selector": "js=x"}))
        ask = await recv(self.browser_reader)
        self.assertEqual(ask["method"], CONTEXT_URL_METHOD)
        await send(self.browser_writer, success(ask["id"], {"url": "https://shop.test/cart"}))
        confirm = await recv(self.browser_reader)
        self.assertEqual(confirm["method"], CONFIRM_METHOD)
        self.assertEqual(confirm["params"], {"action": "evalMainWorld",
                                             "method": "input.click",
                                             "url": "https://shop.test/cart"})

    async def test_a_css_locator_is_not_risky_so_nothing_is_asked(self):
        await send(self.cli_writer, command(1, "input.click", {"selector": "css=button"}))
        forwarded = await recv(self.browser_reader)
        self.assertEqual(forwarded["method"], "input.click")

    async def test_an_unanswerable_url_still_asks_when_nothing_is_denied(self):
        """名单空着时问不到 URL 不算错：照旧弹确认，只是框上写不出站名。"""
        await send(self.cli_writer, command(1, "input.click", {"selector": "js=x"}))
        ask = await recv(self.browser_reader)
        await send(self.browser_writer, success(ask["id"], {"url": None}))
        confirm = await recv(self.browser_reader)
        self.assertEqual(confirm["method"], CONFIRM_METHOD)
        self.assertIsNone(confirm["params"]["url"])


class TestDenyListOff(WiringCase):
    """名单空着时不许多花一个往返。"""

    security_cfg = {"confirm_mode": "silent"}

    async def test_no_context_lookup_when_nothing_is_denied(self):
        await send(self.cli_writer, command(1, "input.click", {"selector": "text=x"}))
        forwarded = await recv(self.browser_reader)
        self.assertEqual(forwarded["method"], "input.click")


class TestConfirmModeOverride(WiringCase):
    """`--confirm-mode` 随握手带过来，只能收紧（spec 4.4）。"""

    security_cfg = {"confirm_mode": "silent"}

    async def test_tightening_from_the_command_line_really_asks(self):
        reader, writer, _ = await self.client(ROLE_CLI, confirm_mode="always")
        await self.settle()
        await send(writer, COOKIES)
        ask = await recv(self.browser_reader)
        self.assertEqual(ask["method"], CONFIRM_METHOD)
        await send(self.browser_writer, success(ask["id"], {"approved": True}))
        self.assertEqual((await recv(self.browser_reader))["method"], "storage.getCookies")

    async def test_another_connection_without_the_flag_is_unaffected(self):
        await self.client(ROLE_CLI, confirm_mode="always")
        await self.settle()
        await send(self.cli_writer, COOKIES)  # 这条连接没带 flag，仍是配置里的 silent
        self.assertEqual((await recv(self.browser_reader))["method"], "storage.getCookies")


class TestConfirmModeCannotLoosen(WiringCase):
    """配置写死 always，命令行想换 silent —— 拒绝，且 daemon 不倒。"""

    security_cfg = {"confirm_mode": "always"}

    async def test_a_loosening_flag_fails_the_command(self):
        reader, writer, _ = await self.client(ROLE_CLI, confirm_mode="silent")
        await self.settle()
        await send(writer, COOKIES)
        reply = await recv(reader)
        self.assertEqual((reply["type"], reply["error"]), ("error", ERR_INVALID_ARGUMENT))
        self.assertIn("只能收紧不能放宽", reply["message"])
        with self.assertRaises(asyncio.TimeoutError):
            await recv(self.browser_reader, timeout=0.3)
        self.assertTrue(probe(self.path), "daemon 必须还活着")


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


class TestBadConfig(WiringCase):
    """配置写错只让指令失败，不能把 daemon 带倒。"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.daemon.security = None
        self.daemon.override_mode = "silent"
        (pathlib.Path(self.tmp) / "browse.yaml").write_text("confirm_mode: always\n")

    async def test_a_loosening_override_fails_the_command_not_the_daemon(self):
        with mock.patch("lib.browse_security._STORE.default_path",
                        return_value=pathlib.Path(self.tmp) / "browse.yaml"):
            await send(self.cli_writer, COOKIES)
            reply = await recv(self.cli_reader)
        self.assertEqual((reply["type"], reply["error"]), ("error", ERR_INVALID_ARGUMENT))
        self.assertIn("只能收紧不能放宽", reply["message"])
        self.assertTrue(probe(self.path), "daemon 必须还活着")


class TestApprovalsPanel(WiringCase):
    """插件面板经 daemon 读写 per_domain 免确认名单（spec 4.5）。"""

    security_cfg = {"confirm_mode": "per_domain"}

    async def ask(self, cid: int, method: str, params: dict | None = None) -> dict:
        await send(self.browser_writer, command(cid, method, params or {}))
        return await recv(self.browser_reader)

    async def test_list_approve_and_revoke_round_trip(self):
        self.assertEqual(await self.ask(1, APPROVALS_LIST), success(1, {"domains": []}))
        self.assertEqual(await self.ask(2, APPROVALS_APPROVE, {"domain": "shop.test"}),
                         success(2, {"domains": ["shop.test"]}))
        self.assertEqual(await self.ask(3, APPROVALS_REVOKE, {"domain": "shop.test"}),
                         success(3, {"domains": []}))

    async def test_approving_from_the_panel_really_silences_the_confirmation(self):
        await self.ask(1, APPROVALS_APPROVE, {"domain": "bank.test"})
        await send(self.cli_writer, COOKIES)
        self.assertEqual((await recv(self.browser_reader))["method"], "storage.getCookies")

    async def test_a_missing_domain_is_an_argument_error(self):
        reply = await self.ask(1, APPROVALS_APPROVE, {})
        self.assertEqual((reply["type"], reply["error"]), ("error", ERR_INVALID_ARGUMENT))

    async def test_an_unknown_browser_command_is_refused_not_forwarded(self):
        reply = await self.ask(1, "lg:nonsense.do")
        self.assertEqual(reply["error"], ERR_UNKNOWN_COMMAND)


class TestConfigPanel(WiringCase):
    """扩展设置页经 daemon 读写 browse.yaml（spec 4.4 / 4.6）。

    这条链路的要害是「只有一份配置」：设置页写的必须是 CLI 读的那个文件，写完 daemon
    必须按新配置办事。所以下面既断言回包，也断言文件内容和后续指令的行为。
    """

    security_cfg = {"confirm_mode": "silent"}

    async def ask(self, cid: int, method: str, params: dict | None = None) -> dict:
        await send(self.browser_writer, command(cid, method, params or {}))
        return await recv(self.browser_reader)

    def saved(self) -> dict:
        import yaml

        path = pathlib.Path(self.tmp) / "browse.yaml"
        return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}

    async def test_get_returns_the_effective_config_and_the_real_file_path(self):
        reply = await self.ask(1, CONFIG_GET)
        self.assertEqual(reply["type"], "success")
        # 文件还不存在时给的是默认值，不是空对象 —— 设置页照着画的就是实际在生效的策略
        self.assertEqual(reply["result"]["config"], {
            "confirm_mode": "silent",
            "deny_domains": [],
            "approved_domains": [],
            "audit": True,
            "audit_retention_days": 7,
        })
        self.assertEqual(reply["result"]["path"], str(pathlib.Path(self.tmp) / "browse.yaml"))

    async def test_set_round_trips_through_the_file_the_cli_reads(self):
        reply = await self.ask(1, CONFIG_SET, {
            "confirm_mode": "always",
            "deny_domains": ["*.Bank.test", " ", "bank.test"],
            "audit": False,
            "audit_retention_days": 0,
        })
        self.assertEqual(reply["type"], "success")
        self.assertEqual(reply["result"]["config"], {
            "confirm_mode": "always",
            "deny_domains": ["bank.test"],
            "approved_domains": [],
            "audit": False,
            "audit_retention_days": 0,
        })
        self.assertEqual(self.saved()["confirm_mode"], "always")
        self.assertEqual(await self.ask(2, CONFIG_GET), reply | {"id": 2})

    async def test_saving_really_changes_what_the_next_command_does(self):
        """改完不生效是这条链路最危险的 bug：用户以为关了，其实还开着。"""
        await self.ask(1, CONFIG_SET, {"deny_domains": ["bank.test"]})
        await send(self.cli_writer, COOKIES)
        reply = await recv(self.cli_reader)
        self.assertEqual(reply["type"], "error", "刚拉黑的域名必须当场就被拒")
        self.assertIn("bank.test", reply["message"])

    async def test_unknown_fields_are_ignored_not_written(self):
        await self.ask(1, CONFIG_SET, {"audit": False, "whatever": "x", "token": "secret"})
        self.assertEqual(set(self.saved()), {"audit"})

    async def test_an_illegal_confirm_mode_is_refused_and_nothing_is_written(self):
        reply = await self.ask(1, CONFIG_SET, {"confirm_mode": "loud"})
        self.assertEqual((reply["type"], reply["error"]), ("error", ERR_INVALID_ARGUMENT))
        self.assertEqual(self.saved(), {}, "校验没过就一个字都不该落盘")

    async def test_illegal_types_are_refused(self):
        for params in ({"deny_domains": "bank.test"},        # 字符串不是列表
                       {"deny_domains": [1]},                # 列表里不是域名
                       {"audit": "yes"},                     # 不是布尔
                       {"audit_retention_days": "7"},        # 不是整数
                       {"audit_retention_days": True}):      # bool 是 int 的子类，要挡住
            reply = await self.ask(1, CONFIG_SET, params)
            self.assertEqual(reply["error"], ERR_INVALID_ARGUMENT, params)
        self.assertEqual(self.saved(), {})

    async def test_the_panel_and_the_settings_page_share_one_approved_list(self):
        await self.ask(1, APPROVALS_APPROVE, {"domain": "shop.test"})
        reply = await self.ask(2, CONFIG_GET)
        self.assertEqual(reply["result"]["config"]["approved_domains"], ["shop.test"])


class DeadWriter:
    """一个写就炸的 writer：模拟「刚决定要发，连接就没了」那一瞬间。"""

    def write(self, _data):
        raise ConnectionError("对端没了")

    async def drain(self):
        pass


class TestFailClosed(DaemonCase):
    """连接在最不巧的那一刻断掉时，每条路都必须倒向拒绝，不能倒向放行。"""

    security_cfg = {"confirm_mode": "always"}

    async def test_asking_with_no_browser_at_all_is_a_refusal(self):
        self.assertIsNone(self.daemon._browser)
        self.assertIsNone(await self.daemon._ask(
            CONFIRM_METHOD,
            {"action": "readCookies", "method": "storage.getCookies", "url": "a.test"}))

    async def test_a_confirmation_that_cannot_be_sent_is_a_refusal(self):
        self.daemon._browser = _Conn(id=99, role=ROLE_NATIVE_HOST, writer=DeadWriter())
        self.assertIsNone(await self.daemon._ask(
            CONFIRM_METHOD,
            {"action": "readCookies", "method": "storage.getCookies", "url": "a.test"}))
        self.assertEqual(self.daemon._pending, {}, "发不出去就不能留下一条永远不回的 pending")

    async def test_a_command_that_cannot_be_forwarded_fails_and_is_audited(self):
        cli_reader, cli_writer, _ = await self.client(ROLE_CLI)
        await self.settle()
        self.daemon._browser = _Conn(id=99, role=ROLE_NATIVE_HOST, writer=DeadWriter())
        # 不走高危方法，免得先卡在确认上
        await send(cli_writer, command(1, "browsingContext.getTree"))
        self.assertEqual((await recv(cli_reader))["error"], ERR_NOT_CONNECTED)
        self.assertEqual(self.audit_lines()[0]["result"], "error")

    async def test_auditing_before_the_security_layer_exists_is_a_no_op(self):
        self.daemon.security = None
        self.daemon._audit("storage.getCookies", {}, None, result="success")
        self.assertEqual(self.audit_lines(), [])

    async def test_an_envelope_the_browser_should_never_send_is_dropped(self):
        conn = _Conn(id=1, role=ROLE_NATIVE_HOST, writer=DeadWriter())
        await self.daemon._from_browser(conn, {"type": "hello", "id": 1})


if __name__ == "__main__":
    unittest.main()

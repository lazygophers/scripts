"""browse native host 测试：双向转发、daemon 拉起、stdout 零污染。

stdin/stdout 全用真管道和内存缓冲注入，**不 fork 浏览器**。最关键的一组断言是
「stdout 里除了合法帧一个字节都没有」—— 这一环被日志污染就直接搞坏通信。
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import lib.browse_native_host as m_host  # noqa: E402
from lib.browse_daemon import MAX_HELLO_BYTES, pack, read_frame  # noqa: E402
from lib.browse_native_host import (  # noqa: E402
    browser_of,
    daemon_command,
    ensure_daemon,
    main,
    run,
)
from lib.browse_protocol import (  # noqa: E402
    MAX_INCOMING_FRAME_BYTES,
    MAX_OUTGOING_FRAME_BYTES,
    ProtocolError,
    command,
    decode_frames,
    success,
)

TIMEOUT = 5.0


def _write_all(fd: int, payload: bytes) -> None:
    """os.write 可能只写一部分，补齐为止。"""
    while payload:
        payload = payload[os.write(fd, payload):]


class Sink:
    """假 stdout：只认 write/flush，攒着字节供断言。"""

    def __init__(self):
        self.data = bytearray()
        self.flushes = 0

    def write(self, chunk: bytes) -> int:
        self.data += chunk
        return len(chunk)

    def flush(self) -> None:
        self.flushes += 1

    def frames(self) -> list[dict]:
        """把攒到的字节当协议流解开。有任何杂质都会在这里炸。"""
        messages, rest = decode_frames(bytes(self.data))
        if rest:
            raise AssertionError(f"stdout 末尾有 {len(rest)} 字节残留: {rest[:64]!r}")
        return messages


class FakeDaemon:
    """只做握手 + 收发帧的假 daemon，够验转发了。"""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.received: list[dict] = []
        self.connected = asyncio.Event()
        self.got = asyncio.Event()
        self.writer: asyncio.StreamWriter | None = None
        self.hello: dict | None = None
        self.server: asyncio.AbstractServer | None = None
        self.clients: list[asyncio.StreamWriter] = []

    async def start(self) -> None:
        self.server = await asyncio.start_unix_server(self._handle, path=str(self.path))

    async def stop(self) -> None:
        for writer in self.clients:  # 先踢客户端，handler 才会退出
            writer.close()
        self.clients.clear()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.clients.append(writer)
        try:
            self.hello = await read_frame(reader, MAX_HELLO_BYTES)
        except asyncio.IncompleteReadError:
            return  # probe() 连上就关，不是客户端
        writer.write(pack({"type": "hello-ack", "connectionId": 7}, MAX_HELLO_BYTES))
        await writer.drain()
        self.writer = writer
        self.connected.set()
        buf = b""
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                return
            buf += chunk
            messages, buf = decode_frames(buf)
            if messages:
                self.received.extend(messages)
                self.got.set()

    async def push(self, message: dict) -> None:
        """daemon → native host 方向发一帧。"""
        assert self.writer is not None
        self.writer.write(pack(message, MAX_INCOMING_FRAME_BYTES))
        await self.writer.drain()

    async def next_received(self) -> dict:
        await asyncio.wait_for(self.got.wait(), TIMEOUT)
        self.got.clear()
        return self.received[-1]


class HostCase(unittest.IsolatedAsyncioTestCase):
    """起一个假 daemon + 一对真管道，run() 跑在后台任务里。"""

    async def asyncSetUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.sock = self.tmp / "browse.sock"
        self.daemon = FakeDaemon(self.sock)
        await self.daemon.start()
        self.sink = Sink()
        read_fd, self.write_fd = os.pipe()
        self.stdin = os.fdopen(read_fd, "rb", 0)
        self.task = asyncio.create_task(run(path=self.sock, stdin=self.stdin, stdout=self.sink))
        await asyncio.wait_for(self.daemon.connected.wait(), TIMEOUT)

    async def asyncTearDown(self):
        try:
            os.close(self.write_fd)
        except OSError:
            pass  # 用例自己关过了
        self.task.cancel()
        try:
            await asyncio.wait_for(self.task, TIMEOUT)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        await self.daemon.stop()
        self.stdin.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def feed(self, message: dict) -> None:
        """扩展 → native host 方向写一帧进 stdin 管道。"""
        await self.feed_raw(pack(message, MAX_INCOMING_FRAME_BYTES))

    async def feed_raw(self, payload: bytes) -> None:
        # 管道缓冲只有几十 KB，截图那条帧会写到阻塞；扔进线程写，事件循环才转得动
        await asyncio.to_thread(_write_all, self.write_fd, payload)


class TestHandshake(HostCase):
    async def test_registers_as_native_host(self):
        self.assertEqual(self.daemon.hello, {"type": "hello", "role": "native-host"})


class TestDaemonToBrowser(HostCase):
    async def test_command_reaches_stdout_as_a_frame(self):
        await self.daemon.push(command(42, "browsingContext.navigate", {"url": "https://a.test"}))
        await self._wait_stdout()
        self.assertEqual(self.sink.frames(),
                         [command(42, "browsingContext.navigate", {"url": "https://a.test"})])

    async def test_keeps_the_daemon_global_id_untouched(self):
        await self.daemon.push(command(99999, "script.evaluate", {}))
        await self._wait_stdout()
        self.assertEqual(self.sink.frames()[0]["id"], 99999)

    async def test_stdout_holds_nothing_but_frames(self):
        for i in range(1, 6):
            await self.daemon.push(command(i, "browsingContext.getTree", {}))
        await self._wait_stdout(count=5)
        self.assertEqual([m["id"] for m in self.sink.frames()], [1, 2, 3, 4, 5])
        # 逐帧核对长度前缀，确认中间没有插进任何非帧字节
        buf, ids = bytes(self.sink.data), []
        while buf:
            (length,) = struct.unpack("<I", buf[:4])
            ids.append(length)
            buf = buf[4 + length:]
        self.assertEqual(len(ids), 5)

    async def test_oversized_command_to_browser_is_refused_not_written(self):
        # host → 浏览器方向卡 1 MB。超了不许写半截出去，否则协议流就废了。
        await self.daemon.push(command(1, "script.evaluate",
                                       {"blob": "x" * (MAX_OUTGOING_FRAME_BYTES + 10)}))
        await asyncio.wait_for(self.task, TIMEOUT)
        self.assertEqual(self.sink.frames(), [])

    async def _wait_stdout(self, count: int = 1) -> None:
        for _ in range(int(TIMEOUT / 0.01)):
            await asyncio.sleep(0.01)
            try:  # 等待期间可能只到半帧，解不开是正常的
                if len(self.sink.frames()) >= count:
                    return
            except (ProtocolError, AssertionError):
                continue
        raise AssertionError(f"stdout 没等到 {count} 帧: {bytes(self.sink.data)!r}")


class TestBrowserToDaemon(HostCase):
    async def test_success_reaches_daemon(self):
        await self.feed(success(42, {"ok": True}))
        self.assertEqual(await self.daemon.next_received(), success(42, {"ok": True}))

    async def test_event_reaches_daemon(self):
        await self.feed({"type": "event", "method": "network.responseCompleted",
                         "params": {"a": 1}})
        got = await self.daemon.next_received()
        self.assertEqual(got["method"], "network.responseCompleted")

    async def test_screenshot_sized_reply_gets_through(self):
        # 这一条专门盯「两个方向的上限被写反」：截图必然超 host → 浏览器的 1 MB，
        # 但走的是浏览器 → daemon 方向，上限是 64 MB。写反了这里必挂。
        big = success(7, {"data": "A" * (MAX_OUTGOING_FRAME_BYTES + 4096)})
        await self.feed(big)
        self.assertEqual(await self.daemon.next_received(), big)

    async def test_split_frame_is_reassembled(self):
        blob = pack(success(3, {"v": "hello"}), MAX_INCOMING_FRAME_BYTES)
        await self.feed_raw(blob[:3])
        await asyncio.sleep(0.05)
        self.assertEqual(self.daemon.received, [])
        await self.feed_raw(blob[3:])
        self.assertEqual(await self.daemon.next_received(), success(3, {"v": "hello"}))

    async def test_bad_frame_disconnects_instead_of_resyncing(self):
        await self.feed_raw(struct.pack("<I", 5) + b"notjs")
        await asyncio.wait_for(self.task, TIMEOUT)
        self.assertEqual(self.daemon.received, [])

    async def test_endless_partial_frame_hits_the_buffer_ceiling(self):
        # 单帧 64 MB 的上限拦不住「声明一个大帧但永远发不完」，缓冲会无限涨。
        # 上限调小只是为了别在测试里真灌 64 MB，被测的是同一条分支。
        with mock.patch.object(m_host, "MAX_BUFFER_BYTES", 4096):
            await self.feed_raw(struct.pack("<I", 1 << 20) + b"x" * 8192)
            self.assertEqual(await asyncio.wait_for(self.task, TIMEOUT), 0)
        self.assertEqual(self.daemon.received, [])

    async def test_eof_on_stdin_ends_the_process(self):
        os.close(self.write_fd)
        self.assertEqual(await asyncio.wait_for(self.task, TIMEOUT), 0)


class TestDaemonGone(unittest.IsolatedAsyncioTestCase):
    """daemon 断开时 native host 自己退出 —— 无状态，浏览器重连即可。"""

    async def test_returns_when_daemon_closes(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        try:
            daemon = FakeDaemon(tmp / "browse.sock")
            await daemon.start()
            sink = Sink()
            read_fd, write_fd = os.pipe()
            stdin = os.fdopen(read_fd, "rb", 0)
            task = asyncio.create_task(run(path=daemon.path, stdin=stdin, stdout=sink))
            await asyncio.wait_for(daemon.connected.wait(), TIMEOUT)
            daemon.writer.close()
            self.assertEqual(await asyncio.wait_for(task, TIMEOUT), 0)
            self.assertEqual(bytes(sink.data), b"")
            os.close(write_fd)
            stdin.close()
            await daemon.stop()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestHandshakeRejected(unittest.IsolatedAsyncioTestCase):
    """socket 在、但握手回不来（daemon 正在退出）：报错退出，stdout 仍不许有字节。"""

    async def test_returns_1_with_clean_stdout(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        sink = Sink()
        read_fd, write_fd = os.pipe()
        stdin = os.fdopen(read_fd, "rb", 0)

        async def slam(reader, writer):
            writer.close()

        server = await asyncio.start_unix_server(slam, path=str(tmp / "browse.sock"))
        try:
            self.assertEqual(await run(path=tmp / "browse.sock", stdin=stdin, stdout=sink), 1)
            self.assertEqual(bytes(sink.data), b"")
        finally:
            server.close()
            await server.wait_closed()
            stdin.close()
            os.close(write_fd)
            shutil.rmtree(tmp, ignore_errors=True)


class TestEnsureDaemon(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.sock = self.tmp / "browse.sock"
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    async def test_running_daemon_is_not_respawned(self):
        daemon = FakeDaemon(self.sock)
        await daemon.start()
        try:
            with mock.patch("subprocess.Popen") as popen:
                self.assertTrue(await ensure_daemon(self.sock, ["should-not-run"]))
            popen.assert_not_called()
        finally:
            await daemon.stop()

    async def test_spawns_detached_with_stdout_swallowed(self):
        # start_new_session 是硬要求：我们是浏览器的子进程，不脱离会话 daemon 会陪葬。
        # stdout=DEVNULL 同样是硬要求：继承我们的 stdout 就等于往协议流里灌日志。
        with mock.patch("subprocess.Popen") as popen:
            self.assertFalse(await ensure_daemon(self.sock, ["noop"], timeout=0.15))
        kwargs = popen.call_args.kwargs
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(popen.call_args.args[0], ["noop"])

    async def test_waits_for_a_really_spawned_daemon(self):
        script = (
            "import socket,sys,time\n"
            "time.sleep(0.2)\n"
            "s=socket.socket(socket.AF_UNIX);s.bind(sys.argv[1]);s.listen(1)\n"
            "time.sleep(1.5)\n"
        )
        self.assertTrue(await ensure_daemon(
            self.sock, [sys.executable, "-c", script, str(self.sock)], timeout=TIMEOUT))
        self.assertTrue(self.sock.exists())

    async def test_gives_up_when_the_socket_never_appears(self):
        self.assertFalse(await ensure_daemon(
            self.sock, [sys.executable, "-c", "pass"], timeout=0.2))

    async def test_run_bails_out_without_touching_stdout(self):
        sink = Sink()
        read_fd, write_fd = os.pipe()
        stdin = os.fdopen(read_fd, "rb", 0)
        try:
            code = await run(path=self.sock, stdin=stdin, stdout=sink,
                             command=[sys.executable, "-c", "pass"])
        finally:
            stdin.close()
            os.close(write_fd)
        self.assertEqual(code, 1)
        self.assertEqual(bytes(sink.data), b"")


class TestDaemonCommand(unittest.TestCase):
    def test_runs_the_given_entrypoint_through_the_current_interpreter(self):
        argv = daemon_command("bin/browse")
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith("bin/browse"))
        self.assertTrue(pathlib.Path(argv[1]).is_absolute())
        self.assertEqual(argv[2:], ["daemon", "start"])

    def test_falls_back_to_sys_argv(self):
        with mock.patch.object(sys, "argv", ["bin/browse", "--native-host"]):
            self.assertEqual(daemon_command(), daemon_command("bin/browse"))


class TestMain(unittest.TestCase):
    def test_main_passes_argv0_on_as_the_daemon_entrypoint(self):
        # patch 认出 run 是 async def，给的是 AsyncMock，所以 return_value 就是 await 的结果
        with mock.patch("lib.browse_native_host.run", return_value=3) as fn:
            self.assertEqual(main(["bin/browse", "--native-host"]), 3)
        fn.assert_called_once_with(command=daemon_command("bin/browse"), browser="")


if __name__ == "__main__":
    unittest.main()


class TestBrowserIdentity(unittest.TestCase):
    """native host 自己代表哪个浏览器 —— 由 wrapper 写死的 `--browser` 决定。

    不能靠扩展猜：Brave 的 User-Agent 伪装成 Chrome，Edge 只差一个 `Edg/`。
    """

    def test_it_reads_the_browser_from_its_own_argv(self):
        self.assertEqual(browser_of(["browse", "--native-host", "--browser", "brave"]), "brave")
        self.assertEqual(browser_of(["browse", "--native-host", "--browser=edge"]), "edge")

    def test_the_old_generic_wrapper_has_none_and_that_is_fine(self):
        # 升级前装的 wrapper 不带这个参数：取到空串，daemon 那边归到 unknown 槽
        self.assertEqual(browser_of(["browse", "--native-host"]), "")
        self.assertEqual(browser_of(["browse", "--native-host", "--browser"]), "",
                         "后面漏了值也不能炸")

    def test_main_hands_the_name_through(self):
        with mock.patch("lib.browse_native_host.run", return_value=0) as fn:
            main(["bin/browse", "--native-host", "--browser", "chrome"])
        fn.assert_called_once_with(command=daemon_command("bin/browse"), browser="chrome")

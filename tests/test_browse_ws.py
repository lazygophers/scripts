"""browse_ws：RFC6455 最小子集的帧编解码与握手。全内存/回环，无网络外依赖。"""

from __future__ import annotations

import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib import browse_ws


class TestCodec(unittest.TestCase):
    def test_accept_key_rfc_vector(self) -> None:
        # RFC 6455 §4.2.2 给的算例
        self.assertEqual(browse_ws.accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
                         "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_unmasked_frame_layout(self) -> None:
        # 文本帧 "hi"：FIN+TEXT，短长度
        self.assertEqual(browse_ws.encode_frame(browse_ws.OP_TEXT, b"hi"),
                         b"\x81\x02hi")

    def test_masked_frame_round_trip(self) -> None:
        frame = browse_ws.encode_frame(browse_ws.OP_TEXT, "你好".encode(), mask=True)
        # 掩码帧的布局：0x81 | 0x80，长度带掩码位
        self.assertEqual(frame[0], 0x81)
        self.assertEqual(frame[1], 0x80 | 6)

    def test_extended_length_variants(self) -> None:
        for size in (125, 126, 65535, 65536):
            frame = browse_ws.encode_frame(browse_ws.OP_TEXT, b"x" * size)
            if size < 126:
                self.assertEqual(frame[1], size)
            elif size < 65536:
                self.assertEqual(frame[1], 126)
            else:
                self.assertEqual(frame[1], 127)


class TestHandshakeAndFrames(unittest.TestCase):
    """起一个真的 WS 服务端（回环），用测试客户端走完整握手 + 收发。
    服务端和客户端必须跑在同一个事件循环里（server 绑定它的 loop）。"""

    def _run(self, origin_allowed, body) -> object:
        async def scenario() -> object:
            async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
                try:
                    await browse_ws.server_handshake(reader, writer, origin_allowed=origin_allowed)
                    while True:
                        text = await browse_ws.read_message(reader, writer, 1 << 20)
                        await browse_ws.send_text(writer, text.upper())
                except (ConnectionError, asyncio.IncompleteReadError, browse_ws.WsClosed, OSError):
                    pass

            server = await asyncio.start_server(
                handle, "127.0.0.1", 0, limit=browse_ws.MAX_HANDSHAKE_BYTES)
            try:
                port = server.sockets[0].getsockname()[1]
                return await body(port)
            finally:
                server.close()
                await server.wait_closed()
        return asyncio.run(scenario())

    def test_echo_round_trip_masked(self) -> None:
        async def body(port: int):
            reader, writer = await browse_ws.client_connect("127.0.0.1", port)
            await browse_ws.client_send(writer, '{"type":"hello"}')
            got = await browse_ws.read_message(reader, writer, 1 << 20)
            writer.close()
            return got
        self.assertEqual(self._run(lambda origin: True, body), '{"TYPE":"HELLO"}')

    def test_origin_denied(self) -> None:
        async def body(port: int):
            try:
                await browse_ws.client_connect("127.0.0.1", port, origin="http://evil.test")
                return "connected"
            except ConnectionError as exc:
                return str(exc)
        self.assertIn("403", self._run(lambda origin: origin == "chrome-extension://ok/", body))

    def test_ping_gets_pong_and_close_raises(self) -> None:
        async def body(port: int):
            reader, writer = await browse_ws.client_connect("127.0.0.1", port)
            writer.write(browse_ws.encode_frame(browse_ws.OP_PING, b"pp", mask=True))
            await writer.drain()
            writer.write(browse_ws.encode_frame(browse_ws.OP_TEXT, b"done", mask=True))
            await writer.drain()
            text = await browse_ws.read_message(reader, writer, 1 << 20)
            writer.write(browse_ws.encode_frame(browse_ws.OP_CLOSE, b"", mask=True))
            await writer.drain()
            try:
                await browse_ws.read_message(reader, writer, 1 << 20)
                return text, "no-close"
            except browse_ws.WsClosed:
                return text, "closed"
        self.assertEqual(self._run(lambda origin: True, body), ("DONE", "closed"))


if __name__ == "__main__":
    unittest.main()

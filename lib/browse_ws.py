"""browse bridge 的 WebSocket 传输层 —— 纯 stdlib 实现 RFC 6455 的最小子集。

2026-09-15 连接层重做（用户批准的 Trae-style browser bridge 方案）：扩展不再经
native messaging（浏览器 fork host + stdin/stdout），而是自己连本地 bridge 的
WebSocket。为什么自己写而不用 websockets/aiohttp：本仓库 Python 侧零第三方依赖
（`lib/email.py` 同样的决定），而这里需要的只是服务端握手 + 文本帧收发 + ping/pong。

只实现够用的部分：
- 服务端握手（HTTP Upgrade + Sec-WebSocket-Accept）
- 文本帧，允许分片（continuation）
- ping→pong 自动应答；close→抛 `WsClosed`
- 客户端 helper（测试用）：握手 + 发帧带掩码（RFC 6455 §5.1，客户端→服务端强制）

不实现：permessage-deflate、二进制消息（协议帧全是 JSON 文本）、close 状态码语义。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import struct

# RFC 6455 §1.3：握手里那个固定 GUID，算 accept key 用。
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

MAX_HANDSHAKE_BYTES = 8192


def accept_key(key: str) -> str:
    """`Sec-WebSocket-Key` → `Sec-WebSocket-Accept`（RFC 6455 §4.2.2）。"""
    digest = hashlib.sha1((key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


class WsClosed(Exception):
    """对端发了 close 帧。"""


async def server_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    origin_allowed,
) -> str | None:
    """读 HTTP Upgrade 请求并回 101。返回请求的 Origin（可能为 None）。

    `origin_allowed(origin) -> bool` 决定放不放行：bridge 用它做扩展 ID 白名单。
    不放行就回 403 并抛 `ConnectionError`——握手没完成，流没有续用的价值。
    """
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        if name:
            headers[name.strip().lower()] = value.strip()
    origin = headers.get("origin")
    if "websocket" not in headers.get("upgrade", "").lower() or "sec-websocket-key" not in headers:
        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        raise ConnectionError("不是 WebSocket 升级请求")
    if not origin_allowed(origin):
        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        raise ConnectionError(f"Origin 不在白名单: {origin!r}")
    writer.write(
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n"
        + f"Sec-WebSocket-Accept: {accept_key(headers['sec-websocket-key'])}\r\n\r\n".encode("ascii")
    )
    await writer.drain()
    return origin


async def read_message(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    limit: int,
) -> str:
    """读一条完整的文本消息。ping 就地应答，close 回一个 close 帧后抛 `WsClosed`。

    `limit` 卡的是**拼好的整条消息**长度：分片可以很多片，但总量得有顶——和
    `browse_protocol` 卡缓冲是同一个思路。
    """
    chunks: list[bytes] = []
    size = 0
    opcode = None
    while True:
        head = await reader.readexactly(2)
        fin = head[0] & 0x80
        code = head[0] & 0x0F
        masked = head[1] & 0x80
        length = head[1] & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", await reader.readexactly(2))
        elif length == 127:
            (length,) = struct.unpack(">Q", await reader.readexactly(8))
        if length > limit:
            raise ConnectionError(f"帧声明 {length} 字节，超过上限 {limit}")
        mask = await reader.readexactly(4) if masked else None
        payload = await reader.readexactly(length) if length else b""
        if mask is not None:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if code == OP_CLOSE:
            await write_frame(writer, OP_CLOSE, payload[:125])
            raise WsClosed("对端关闭")
        if code == OP_PING:
            await write_frame(writer, OP_PONG, payload)
            continue
        if code == OP_PONG:
            continue
        if code not in (OP_TEXT, OP_CONT):
            raise ConnectionError(f"不支持的 opcode: {code}")
        if opcode is None:
            opcode = code
        size += len(payload)
        if size > limit:
            raise ConnectionError(f"消息拼到 {size} 字节，超过上限 {limit}")
        chunks.append(payload)
        if fin:
            return b"".join(chunks).decode("utf-8")


def encode_frame(opcode: int, payload: bytes, *, mask: bool = False) -> bytes:
    """一帧 → 字节。服务端发送不掩码；客户端发送必须掩码（RFC 6455 §5.1）。"""
    flag = 0x80 if mask else 0x00
    first = bytes([0x80 | opcode])
    length = len(payload)
    if length < 126:
        size = bytes([flag | length])
    elif length < 1 << 16:
        size = bytes([flag | 126]) + struct.pack(">H", length)
    else:
        size = bytes([flag | 127]) + struct.pack(">Q", length)
    if not mask:
        return first + size + payload
    key = secrets.token_bytes(4)
    return first + size + key + bytes(b ^ key[i % 4] for i, b in enumerate(payload))


async def write_frame(writer: asyncio.StreamWriter, opcode: int, payload: bytes) -> None:
    writer.write(encode_frame(opcode, payload))
    await writer.drain()


async def send_text(writer: asyncio.StreamWriter, text: str) -> None:
    await write_frame(writer, OP_TEXT, text.encode("utf-8"))


# ------------------------------------------------------------------ 测试用客户端

async def client_connect(
    host: str, port: int, *, origin: str | None = None,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """连上 WS 服务端并完成握手。Origin 可指定以测试白名单。"""
    reader, writer = await asyncio.open_connection(host, port, limit=MAX_HANDSHAKE_BYTES)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET /browse HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        + (f"Origin: {origin}\r\n" if origin else "")
        + "\r\n"
    )
    writer.write(request.encode("ascii"))
    await writer.drain()
    status_line = await reader.readuntil(b"\r\n")
    await reader.readuntil(b"\r\n\r\n")
    if b"101" not in status_line:
        writer.close()
        raise ConnectionError(f"握手被拒: {status_line!r}")
    return reader, writer


async def client_send(writer: asyncio.StreamWriter, text: str) -> None:
    writer.write(encode_frame(OP_TEXT, text.encode("utf-8"), mask=True))
    await writer.drain()


__all__ = [
    "GUID",
    "WsClosed",
    "accept_key",
    "client_connect",
    "client_send",
    "encode_frame",
    "read_message",
    "send_text",
    "server_handshake",
]

"""browse 协议层：WebDriver BiDi 形状的命令信封 + native messaging 帧。

纯数据层：不碰网络、不碰进程、不碰文件。上层（daemon / native host / 扩展）拿
字节流进来，拿信封出去。

信封四种（`.scratch/browser-control-extension/spec.md` 3.3）：

    Command  = { id: <uint>, method: "<module>.<action>", params: {...} }
    Success  = { type: "success", id: <uint>, result: {...} }
    Error    = { type: "error", id: <uint>, error: "<code>", message: "..." }
    Event    = { type: "event", method: "<module>.<action>", params: {...} }

模块名沿用 BiDi（`browsingContext` / `script` / `storage` / `input` / `network`
/ `log`）；本项目私有能力必须带冒号前缀（BiDi §3.3），如 `lg:history.search`。

帧格式（spec 第 8 节，思路取自 `hangwin/mcp-chrome:app/native-server/src/
native-messaging-host.ts:31-90`）：4 字节**小端**长度前缀 + UTF-8 JSON body。
`decode_frames` 是增量的——喂进任意切法的字节流，吐出已经完整的信封和还没凑齐的
剩余缓冲，所以半个帧不会丢也不会被误读。
"""

from __future__ import annotations

import json
import re
import struct

# Chrome 对 native host → 浏览器方向的单帧上限就是 1 MB，超了浏览器直接断连接，
# 所以宁可在自己这边抛错，也别把注定被拒的帧发出去。
MAX_FRAME_BYTES = 1024 * 1024

# 错误码：WebDriver BiDi 标准枚举
ERR_NO_SUCH_ELEMENT = "no such element"
ERR_NO_SUCH_FRAME = "no such frame"
ERR_UNKNOWN_COMMAND = "unknown command"
ERR_UNSUPPORTED_OPERATION = "unsupported operation"
ERR_INVALID_ARGUMENT = "invalid argument"
ERR_TIMEOUT = "timeout"
# 本项目私有，按 BiDi §3.3 带冒号前缀
ERR_NOT_CONNECTED = "lg:browser not connected"
ERR_USER_REJECTED = "lg:user rejected"

ERROR_CODES = frozenset({
    ERR_NO_SUCH_ELEMENT,
    ERR_NO_SUCH_FRAME,
    ERR_UNKNOWN_COMMAND,
    ERR_UNSUPPORTED_OPERATION,
    ERR_INVALID_ARGUMENT,
    ERR_TIMEOUT,
    ERR_NOT_CONNECTED,
    ERR_USER_REJECTED,
})

# `<module>.<action>`，module 可带一段 `<prefix>:` 私有命名空间。
_METHOD_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9]*:)?[A-Za-z][A-Za-z0-9]*\.[A-Za-z][A-Za-z0-9]*$")


class ProtocolError(Exception):
    """信封或帧不合法。message 直接给用户看。"""


# ---------------------------------------------------------------- 校验
def check_method(method) -> str:
    """校验 `<module>.<action>` 形状，返回原值。"""
    if not isinstance(method, str) or not _METHOD_RE.match(method):
        raise ProtocolError(f"method 必须是 <module>.<action> 形状（私有能力带冒号前缀）: {method!r}")
    return method


def check_id(value) -> int:
    """校验命令 id：非负整数（bool 不算）。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError(f"id 必须是非负整数: {value!r}")
    return value


def check_code(code) -> str:
    """校验错误码：标准枚举，或任何带冒号前缀的私有码。"""
    if isinstance(code, str) and (code in ERROR_CODES or ":" in code):
        return code
    raise ProtocolError(f"未知错误码: {code!r}（标准码见 ERROR_CODES，私有码须带冒号前缀）")


def _check_dict(value, field: str) -> dict:
    """可选的对象字段：缺省当空 dict，给了就必须真是 dict。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProtocolError(f"{field} 必须是对象: {value!r}")
    return dict(value)


# ---------------------------------------------------------------- 构造
def command(id: int, method: str, params: dict | None = None) -> dict:
    """造一条 Command。"""
    return {"id": check_id(id), "method": check_method(method), "params": _check_dict(params, "params")}


def success(id: int, result: dict | None = None) -> dict:
    """造一条 Success 回包。"""
    return {"type": "success", "id": check_id(id), "result": _check_dict(result, "result")}


def error(id: int, code: str, message: str) -> dict:
    """造一条 Error 回包。"""
    return {"type": "error", "id": check_id(id), "error": check_code(code), "message": str(message)}


def event(method: str, params: dict | None = None) -> dict:
    """造一条 Event（无 id，浏览器单向推给 daemon）。"""
    return {"type": "event", "method": check_method(method), "params": _check_dict(params, "params")}


# ---------------------------------------------------------------- 解析
def parse_message(obj) -> dict:
    """校验一条已经从 JSON 解出来的信封，返回补齐默认值后的规范形式。

    没有 `type` 字段的当 Command —— BiDi 里命令本来就不带 type。
    """
    if not isinstance(obj, dict):
        raise ProtocolError(f"信封必须是对象: {obj!r}")
    kind = obj.get("type")
    if kind is None:
        return command(obj.get("id"), obj.get("method"), obj.get("params"))
    if kind == "success":
        return success(obj.get("id"), obj.get("result"))
    if kind == "error":
        return error(obj.get("id"), obj.get("error"), obj.get("message", ""))
    if kind == "event":
        return event(obj.get("method"), obj.get("params"))
    raise ProtocolError(f"未知信封类型: {kind!r}")


# ---------------------------------------------------------------- 帧
def encode_frame(message: dict) -> bytes:
    """信封 → 一个完整帧（4 字节小端长度 + UTF-8 JSON）。先校验再编。"""
    body = json.dumps(parse_message(message), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise ProtocolError(f"帧体 {len(body)} 字节，超过上限 {MAX_FRAME_BYTES}")
    return struct.pack("<I", len(body)) + body


def decode_frames(buf: bytes) -> tuple[list[dict], bytes]:
    """增量解帧：返回 (已完整的信封列表, 还没凑齐的剩余字节)。

    调用方把上次的剩余拼上新读到的字节再喂进来即可。长度前缀本身不全、或者 body
    还没到齐，都原样留在剩余里等下一次。
    """
    messages: list[dict] = []
    rest = bytes(buf)
    while len(rest) >= 4:
        (length,) = struct.unpack("<I", rest[:4])
        if length > MAX_FRAME_BYTES:
            raise ProtocolError(f"帧声明 {length} 字节，超过上限 {MAX_FRAME_BYTES}")
        if len(rest) - 4 < length:
            break
        body, rest = rest[4:4 + length], rest[4 + length:]
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"帧体不是合法 UTF-8 JSON: {exc}") from exc
        messages.append(parse_message(payload))
    return messages, rest

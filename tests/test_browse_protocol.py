"""browse 协议层测试：信封校验 + native messaging 增量解帧。"""

from __future__ import annotations

import json
import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.browse_protocol import (  # noqa: E402
    ERR_INVALID_ARGUMENT,
    ERR_NOT_CONNECTED,
    ERROR_CODES,
    MAX_INCOMING_FRAME_BYTES,
    MAX_OUTGOING_FRAME_BYTES,
    ProtocolError,
    check_code,
    check_id,
    check_method,
    command,
    decode_frames,
    encode_frame,
    error,
    event,
    parse_message,
    success,
)


def frame(payload: bytes) -> bytes:
    """手工拼一个帧，绕开 encode_frame 的校验，用来测非法 body。"""
    return struct.pack("<I", len(payload)) + payload


class TestMethodShape(unittest.TestCase):
    def test_accepts_bidi_modules(self):
        for m in ("browsingContext.navigate", "script.evaluate", "storage.getCookies",
                  "input.performActions", "network.addIntercept", "log.entryAdded"):
            self.assertEqual(check_method(m), m)

    def test_accepts_private_colon_prefix(self):
        self.assertEqual(check_method("lg:history.search"), "lg:history.search")

    def test_rejects_bad_shapes(self):
        for bad in ("", "navigate", "a.b.c", ".navigate", "browsingContext.",
                    "lg:.search", ":history.search", "lg:history:more.search",
                    "1module.action", "browsingContext navigate", None, 42, ["a.b"]):
            with self.assertRaises(ProtocolError):
                check_method(bad)


class TestScalarChecks(unittest.TestCase):
    def test_id_accepts_zero_and_positive(self):
        self.assertEqual(check_id(0), 0)
        self.assertEqual(check_id(7), 7)

    def test_id_rejects_bool_negative_and_non_int(self):
        for bad in (True, False, -1, 1.0, "1", None):
            with self.assertRaises(ProtocolError):
                check_id(bad)

    def test_code_accepts_standard_enum(self):
        for code in ERROR_CODES:
            self.assertEqual(check_code(code), code)

    def test_code_accepts_any_namespaced_extension(self):
        self.assertEqual(check_code("xx:something new"), "xx:something new")

    def test_code_rejects_unknown_bare_string(self):
        for bad in ("boom", "", None, 5):
            with self.assertRaises(ProtocolError):
                check_code(bad)


class TestEnvelopes(unittest.TestCase):
    def test_command_defaults_empty_params(self):
        self.assertEqual(
            command(1, "browsingContext.navigate"),
            {"id": 1, "method": "browsingContext.navigate", "params": {}},
        )

    def test_command_copies_params(self):
        params = {"url": "https://example.com"}
        built = command(2, "browsingContext.navigate", params)
        params["url"] = "mutated"
        self.assertEqual(built["params"], {"url": "https://example.com"})

    def test_success_and_error_and_event(self):
        self.assertEqual(success(3, {"ok": True}),
                         {"type": "success", "id": 3, "result": {"ok": True}})
        self.assertEqual(success(3), {"type": "success", "id": 3, "result": {}})
        self.assertEqual(error(4, ERR_NOT_CONNECTED, "没连上"),
                         {"type": "error", "id": 4, "error": ERR_NOT_CONNECTED, "message": "没连上"})
        self.assertEqual(event("log.entryAdded"),
                         {"type": "event", "method": "log.entryAdded", "params": {}})

    def test_non_dict_params_rejected(self):
        with self.assertRaises(ProtocolError):
            command(1, "script.evaluate", ["nope"])
        with self.assertRaises(ProtocolError):
            success(1, "nope")

    def test_error_message_coerced_to_str(self):
        self.assertEqual(error(1, ERR_INVALID_ARGUMENT, 500)["message"], "500")


class TestParseMessage(unittest.TestCase):
    def test_missing_type_is_a_command(self):
        self.assertEqual(
            parse_message({"id": 9, "method": "lg:history.search"}),
            {"id": 9, "method": "lg:history.search", "params": {}},
        )

    def test_round_trip_each_kind(self):
        for msg in (command(1, "script.evaluate", {"expression": "1+1"}),
                    success(1, {"value": 2}),
                    error(1, ERR_INVALID_ARGUMENT, "bad"),
                    event("log.entryAdded", {"level": "warn"})):
            self.assertEqual(parse_message(msg), msg)

    def test_error_without_message_defaults_empty(self):
        parsed = parse_message({"type": "error", "id": 1, "error": ERR_INVALID_ARGUMENT})
        self.assertEqual(parsed["message"], "")

    def test_rejects_non_dict_and_unknown_type(self):
        for bad in ("nope", 5, None, ["a"]):
            with self.assertRaises(ProtocolError):
                parse_message(bad)
        with self.assertRaises(ProtocolError):
            parse_message({"type": "response", "id": 1})


class TestFrames(unittest.TestCase):
    def test_encode_is_little_endian_length_prefix(self):
        raw = encode_frame(command(1, "browsingContext.navigate"))
        self.assertEqual(struct.unpack("<I", raw[:4])[0], len(raw) - 4)
        self.assertEqual(json.loads(raw[4:].decode("utf-8"))["id"], 1)

    def test_encode_validates_before_packing(self):
        with self.assertRaises(ProtocolError):
            encode_frame({"id": 1, "method": "nope"})

    def test_encode_rejects_body_over_one_megabyte(self):
        """出站是 host → 浏览器，Chrome 那头硬卡 1 MB。"""
        with self.assertRaises(ProtocolError) as ctx:
            encode_frame(command(1, "script.evaluate", {"src": "x" * (MAX_OUTGOING_FRAME_BYTES + 1)}))
        self.assertIn("host → 浏览器", str(ctx.exception))

    def test_decode_accepts_two_megabyte_frame(self):
        """入站是浏览器 → host，截图就走这个方向，1 MB 卡不住它。"""
        big = "x" * (2 * 1024 * 1024)
        body = json.dumps({"type": "success", "id": 1, "result": {"png": big}}).encode("utf-8")
        self.assertGreater(len(body), MAX_OUTGOING_FRAME_BYTES)
        messages, rest = decode_frames(frame(body))
        self.assertEqual(messages[0]["result"]["png"], big)
        self.assertEqual(rest, b"")

    def test_encode_keeps_unicode_raw(self):
        raw = encode_frame(command(1, "script.evaluate", {"t": "中文"}))
        self.assertIn("中文".encode(), raw)

    def test_decode_empty_buffer(self):
        self.assertEqual(decode_frames(b""), ([], b""))

    def test_decode_multiple_frames_in_one_chunk(self):
        blob = encode_frame(command(1, "script.evaluate")) + encode_frame(success(1, {"v": 2}))
        messages, rest = decode_frames(blob)
        self.assertEqual([m.get("id") for m in messages], [1, 1])
        self.assertEqual(rest, b"")

    def test_decode_half_frame_byte_by_byte(self):
        """逐字节喂进去，只有最后一字节到齐时才吐出信封。"""
        blob = encode_frame(command(42, "lg:history.search", {"q": "python"}))
        buf, seen = b"", []
        for i in range(len(blob)):
            messages, buf = decode_frames(buf + blob[i:i + 1])
            seen.extend(messages)
            if i < len(blob) - 1:
                self.assertEqual(seen, [])
        self.assertEqual(seen, [{"id": 42, "method": "lg:history.search", "params": {"q": "python"}}])
        self.assertEqual(buf, b"")

    def test_decode_partial_length_prefix_is_kept(self):
        messages, rest = decode_frames(b"\x01\x02")
        self.assertEqual(messages, [])
        self.assertEqual(rest, b"\x01\x02")

    def test_decode_frame_spanning_chunks_keeps_tail(self):
        blob = encode_frame(success(7, {"ok": True})) + encode_frame(event("log.entryAdded"))
        cut = len(blob) - 3
        messages, rest = decode_frames(blob[:cut])
        self.assertEqual([m["id"] for m in messages], [7])
        messages2, rest2 = decode_frames(rest + blob[cut:])
        self.assertEqual(messages2, [event("log.entryAdded")])
        self.assertEqual(rest2, b"")

    def test_decode_rejects_declared_length_over_incoming_cap(self):
        """声明长度来自对端，超了 64 MB 直接拒，不给它分配内存的机会。"""
        with self.assertRaises(ProtocolError) as ctx:
            decode_frames(struct.pack("<I", MAX_INCOMING_FRAME_BYTES + 1))
        self.assertIn("浏览器 → host", str(ctx.exception))

    def test_decode_rejects_bad_json(self):
        with self.assertRaises(ProtocolError):
            decode_frames(frame(b"{not json"))

    def test_decode_rejects_bad_utf8(self):
        with self.assertRaises(ProtocolError):
            decode_frames(frame(b"\xff\xfe"))

    def test_decode_rejects_zero_length_frame(self):
        with self.assertRaises(ProtocolError):
            decode_frames(frame(b""))

    def test_decode_rejects_bad_method_shape_in_wire_data(self):
        with self.assertRaises(ProtocolError):
            decode_frames(frame(b'{"id":1,"method":"navigate"}'))


if __name__ == "__main__":
    unittest.main()

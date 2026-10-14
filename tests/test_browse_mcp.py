"""browse mcp server：协议面 + 工具路由（工具执行走 _call 的 mock）。"""
import io
import json
import unittest
from unittest import mock

from lib import browse_mcp


def rpc(method, msg_id=1, **params):
    return json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})


class TestProtocol(unittest.TestCase):
    def _serve(self, *lines):
        out = io.StringIO()
        code = browse_mcp.serve(io.StringIO("\n".join(lines) + "\n"), out)
        self.assertEqual(code, 0)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_initialize_echoes_protocol_and_lists_capabilities(self):
        [resp] = self._serve(rpc("initialize", protocolVersion="2025-03-26"))
        self.assertEqual(resp["result"]["serverInfo"]["name"], "browse")
        self.assertEqual(resp["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_ping_and_notifications(self):
        resps = self._serve(rpc("ping", 7), json.dumps({"jsonrpc": "2.0", "method": "initialized"}))
        self.assertEqual(resps, [{"jsonrpc": "2.0", "id": 7, "result": {}}])

    def test_bad_line_is_dropped_not_fatal(self):
        resps = self._serve("not json", rpc("ping", 2))
        self.assertEqual(len(resps), 1)

    def test_tools_list_has_the_core_surface(self):
        [resp] = self._serve(rpc("tools/list"))
        names = {t["name"] for t in resp["result"]["tools"]}
        for must in ("browse_open", "browse_snapshot", "browse_click", "browse_type",
                     "browse_page_text", "browse_wait", "browse_net_route_add",
                     "browse_console"):
            self.assertIn(must, names)
        for tool in resp["result"]["tools"]:
            self.assertIn("inputSchema", tool)

    def test_unknown_tool_is_error_content_not_a_protocol_error(self):
        [resp] = self._serve(rpc("tools/call", name="browse_frobnicate"))
        self.assertNotIn("error", resp)
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("unknown tool", resp["result"]["content"][0]["text"])

    def test_unknown_method_is_protocol_error(self):
        [resp] = self._serve(rpc("resources/list"))
        self.assertEqual(resp["error"]["code"], -32601)


class TestToolRouting(unittest.TestCase):
    def _call_tool(self, name, args):
        sent = []

        async def fake_call(method, params):
            sent.append((method, params))
            if method == "lg:page.snapshot":
                return {"elements": [{"ref": "@e1", "text": "登录", "css": "#a"}]}
            if method == "script.evaluate":
                return {"result": {"value": "页面正文"}}
            if method == "lg:page.lifecycle":
                return {"readyState": "complete", "timedOut": False}
            return {"ok": True}

        with mock.patch.object(browse_mcp, "_call", side_effect=fake_call):
            out = io.StringIO()
            code = browse_mcp.serve(io.StringIO(rpc(
                "tools/call", name=name, arguments=args) + "\n"), out)
        self.assertEqual(code, 0)
        [resp] = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertFalse(resp["result"].get("isError"), resp)
        return resp["result"]["content"][0]["text"], sent

    def test_click_passes_selector_and_context(self):
        _, sent = self._call_tool("browse_click", {"selector": "@e3", "context": 12})
        self.assertEqual(sent, [("input.click", {"selector": "@e3", "context": "12"})])

    def test_type_defaults_clear_true_omittable(self):
        _, sent = self._call_tool("browse_type", {"selector": "@e1", "text": "hi"})
        self.assertEqual(sent, [("input.type", {"selector": "@e1", "text": "hi"})])
        _, sent = self._call_tool("browse_type", {"selector": "@e1", "text": "hi",
                                                  "clear": False})
        self.assertEqual(sent[-1], ("input.type", {"selector": "@e1", "text": "hi",
                                                   "clear": False}))

    def test_snapshot_delta_and_limit_forwarded(self):
        _, sent = self._call_tool("browse_snapshot", {"delta": True, "limit": 10})
        self.assertEqual(sent, [("lg:page.snapshot", {"delta": True, "limit": 10})])

    def test_page_text_is_nonce_wrapped(self):
        text, _ = self._call_tool("browse_page_text", {})
        payload = json.loads(text)
        self.assertRegex(payload["text"],
                         r"^<<<browse:content:[0-9a-f]{16}>>>\n页面正文\n<<<browse:content:[0-9a-f]{16}>>>$")

    def test_wait_lifecycle_and_text(self):
        _, sent = self._call_tool("browse_wait", {"for": "domcontentloaded"})
        self.assertEqual(sent, [("lg:page.lifecycle", {"waitFor": "domcontentloaded"})])
        text, sent = self._call_tool("browse_wait", {"for": "登录"})
        self.assertEqual(json.loads(text), {"appeared": "登录"})
        self.assertEqual(sent[0][0], "lg:page.snapshot")

    def test_net_route_add(self):
        _, sent = self._call_tool("browse_net_route_add",
                                  {"pattern": "https://api.test/*", "action": "mock",
                                   "body": "{}", "status": 201})
        self.assertEqual(sent, [("lg:net.routeAdd",
                                 {"pattern": "https://api.test/*", "action": "mock",
                                  "body": "{}", "status": 201})])


if __name__ == "__main__":
    unittest.main()

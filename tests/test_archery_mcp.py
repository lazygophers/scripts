"""archery mcp：环境变量客户端 + stdio JSON-RPC 协议面。

不起真网络：serve() 用 StringIO 喂请求行、收回包行；客户端用假对象。
子进程冒烟只跑 initialize / tools/list（懒建客户端，零网络）。
"""
import io
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

from lib.archery import ArcheryError
from lib.archery_mcp import TOOLS, env_client, serve


class TestEnvClient(unittest.TestCase):
    def _env(self, **extra):
        base = {"ARCHERY_URL": "archery.example.com", "ARCHERY_USERNAME": "nico",
                "ARCHERY_PASSWORD": "secret"}
        return {**base, **extra}

    def test_missing_env_names_vars(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ArcheryError) as got:
                env_client()
        self.assertIn("ARCHERY_URL", str(got.exception))
        self.assertIn("ARCHERY_USERNAME", str(got.exception))
        self.assertIn("ARCHERY_PASSWORD", str(got.exception))

    def test_profile_shape(self):
        with mock.patch.dict("os.environ", self._env(ARCHERY_INSECURE="1"), clear=True):
            client = env_client()
        self.assertEqual(client.key, "archery.example.com")
        self.assertEqual(client.profile["url"], "https://archery.example.com")
        self.assertEqual(client.profile["username"], "nico")
        self.assertTrue(client.profile["insecure"])
        self.assertFalse(client._persist_on)

    def test_totp_normalized(self):
        env = self._env(ARCHERY_TOTP_SECRET="otpauth://totp/x?secret=JBSWY3DPEHPK3PXP")
        with mock.patch.dict("os.environ", env, clear=True):
            client = env_client()
        self.assertEqual(client.profile["totp_secret"], "JBSWY3DPEHPK3PXP")

    def test_token_stays_in_memory(self):
        with mock.patch.dict("os.environ", self._env(), clear=True):
            client = env_client()
        with mock.patch("lib.archery.save_config") as save:
            client._store_token("access-1", "refresh-1")
        save.assert_not_called()  # persist=False：换到 token 也不写 archery.yaml
        self.assertEqual(client.profile["token"]["access"], "access-1")


class _FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, path, **params):
        self.calls.append(("GET", path, params))
        return {"data": [{"instance_name": "prod-mysql"}]}

    def post(self, path, body=None, **params):
        self.calls.append(("POST", path, body))
        raise ArcheryError("POST /api/v1/sqlquery/execute/ -> HTTP 404: (空响应体)")

    def web(self, method, path, *, params=None, form=None):
        self.calls.append((method, path, form))
        return {"data": {"column_list": ["1"], "rows": [["1"]]}}


class TestServe(unittest.TestCase):
    def _roundtrip(self, *messages, client=None):
        fake = client or _FakeClient()
        lines = "".join(json.dumps(m) + "\n" for m in messages)
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"ARCHERY_URL": "a.test", "ARCHERY_USERNAME": "u",
                                            "ARCHERY_PASSWORD": "p"}, clear=True):
            with mock.patch("lib.archery_mcp.env_client", return_value=fake):
                rc = serve(io.StringIO(lines), out)
        self.assertEqual(rc, 0)
        return fake, [json.loads(l) for l in out.getvalue().splitlines()]

    def test_initialize_and_tools_list(self):
        _, replies = self._roundtrip(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        self.assertEqual(len(replies), 2)  # notification 不回包
        info, tools = replies
        self.assertEqual(info["result"]["serverInfo"]["name"], "archery")
        self.assertEqual(info["result"]["protocolVersion"], "2025-06-18")
        names = {t["name"] for t in tools["result"]["tools"]}
        self.assertIn("archery_query_execute", names)
        self.assertIn("archery_api", names)

    def test_tool_call_dispatches_and_replies_json(self):
        fake, replies = self._roundtrip(
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
             "params": {"name": "archery_query_instances", "arguments": {"params": {"db_type": "mysql"}}}},
        )
        self.assertEqual(fake.calls, [("GET", "v1/sqlquery/instances/", {"db_type": "mysql"})])
        reply = replies[0]
        self.assertIsNone(reply.get("error"))
        self.assertFalse(reply["result"].get("isError", False))
        self.assertIn("prod-mysql", reply["result"]["content"][0]["text"])

    def test_404_falls_back_to_web(self):
        fake, replies = self._roundtrip(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "archery_query_execute",
                        "arguments": {"sql": "select 1", "instance_name": "x", "db_name": "d"}}},
            client=_FakeClient(),
        )
        # 假客户端 GET 走 API、POST 一律 404：execute 应该回落到网页端 /query/
        self.assertEqual(fake.calls[-1][0], "POST")
        self.assertEqual(fake.calls[-1][1], "/query/")

    def test_archery_error_becomes_is_error(self):
        class Boom(_FakeClient):
            def post(self, path, body=None, **params):
                raise ArcheryError("登录失败 HTTP 401")

        _, replies = self._roundtrip(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "archery_query_execute",
                        "arguments": {"sql": "select 1", "instance_name": "x", "db_name": "d"}}},
            client=Boom(),
        )
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertIn("HTTP 401", replies[0]["result"]["content"][0]["text"])

    def test_unknown_tool_and_method(self):
        _, replies = self._roundtrip(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope"}},
            {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
        )
        self.assertEqual(replies[0]["error"]["code"], -32602)
        self.assertEqual(replies[1]["error"]["code"], -32601)

    def test_missing_required_argument_is_error_not_crash(self):
        _, replies = self._roundtrip(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "archery_query_execute", "arguments": {}}},
        )
        self.assertTrue(replies[0]["result"]["isError"])

    def test_bad_line_ignored(self):
        lines = io.StringIO("not json\n\n" + json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n")
        out = io.StringIO()
        serve(lines, out)
        replies = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual(len(replies), 1)  # 坏行和空行都不回包，只有 ping 回
        self.assertEqual(replies[0]["result"], {})


class TestSchemas(unittest.TestCase):
    def test_every_tool_has_wire_fields(self):
        for tool in TOOLS:
            with self.subTest(tool=tool["name"]):
                self.assertIn("name", tool)
                self.assertTrue(tool["description"])
                self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_query_execute_requires_core_args(self):
        tool = next(t for t in TOOLS if t["name"] == "archery_query_execute")
        self.assertEqual(set(tool["inputSchema"]["required"]),
                         {"sql", "instance_name", "db_name"})


class TestSubprocessSmoke(unittest.TestCase):
    """bin/archery mcp 起得来、握手和列工具不需要网络（客户端懒建）。"""

    def test_handshake_over_stdio(self):
        repo = pathlib.Path(__file__).resolve().parent.parent
        requests = "".join(json.dumps(m) + "\n" for m in [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        proc = subprocess.run(
            [sys.executable, str(repo / "bin" / "archery"), "mcp"],
            input=requests, capture_output=True, text=True, timeout=60,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        replies = [json.loads(l) for l in proc.stdout.splitlines()]
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "archery")
        names = {t["name"] for t in replies[1]["result"]["tools"]}
        self.assertIn("archery_api", names)


if __name__ == "__main__":
    unittest.main()

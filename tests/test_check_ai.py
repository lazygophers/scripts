#!/usr/bin/env python3
"""Tests for lib.check_ai (目标解析 / 官方 URL 固定 / curl 输出解析 / 循环汇总)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.check_ai import build_probe, main, probe_once


class TestBuildProbe(unittest.TestCase):
    def test_unknown_name_raises(self):
        with self.assertRaises(ValueError):
            build_probe("not-a-provider")

    def test_minmax_typo_rejected(self):
        with self.assertRaises(ValueError):
            build_probe("minmax")

    def test_url_passthrough_empty_mode(self):
        res = build_probe("https://example.com/v1/chat")
        self.assertEqual(res["url"], "https://example.com/v1/chat")
        self.assertEqual(res["mode"], "空POST连通")
        self.assertEqual(res["body"], "{}")

    def test_all_presets_official_and_keyless(self):
        # 即使环境里有 token/base_url，也必须打官方 URL 且不带任何认证头
        env = {"ANTHROPIC_AUTH_TOKEN": "sk-test", "ANTHROPIC_BASE_URL": "https://relay.example",
               "OPENAI_API_KEY": "oai", "OPENAI_BASE_URL": "https://relay.example/"}
        with patch.dict("os.environ", env, clear=True):
            for name, official in (
                ("claude", "https://api.anthropic.com/v1/messages"),
                ("codex", "https://api.openai.com/v1/responses"),
                ("openai", "https://api.openai.com/v1/chat/completions"),
                ("glm", "https://open.bigmodel.cn/api/paas/v4/chat/completions"),
                ("kimi", "https://api.moonshot.cn/v1/chat/completions"),
                ("minimax", "https://api.minimax.chat/v1/text/chatcompletion_v2"),
            ):
                res = build_probe(name)
                self.assertEqual(res["url"], official, name)
                self.assertEqual(res["mode"], "空POST连通", name)
                self.assertEqual(res["headers"], {}, name)
                self.assertEqual(res["body"], "{}", name)

    def test_no_token_env_also_official(self):
        with patch.dict("os.environ", {}, clear=True):
            for name in ENDPOINTS_ALL:
                res = build_probe(name)
                self.assertEqual(res["mode"], "空POST连通", name)


ENDPOINTS_ALL = ("claude", "codex", "openai", "glm", "kimi", "minimax")


class _FakeProc:
    def __init__(self, returncode=0, stdout="401 0.123 0.456 1 25", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestProbeOnce(unittest.TestCase):
    def test_success_4xx(self):
        with patch("lib.check_ai.run", return_value=_FakeProc()):
            res = probe_once("https://x", timeout=5, proxy=None)
        self.assertTrue(res["ok"])
        self.assertEqual(res["http_code"], "401")
        self.assertEqual(res["ttfb"], 0.123)
        self.assertEqual(res["size"], 25)

    def test_http_000_is_fail(self):
        with patch("lib.check_ai.run", return_value=_FakeProc(stdout="000 0.000 0.000 0 0")):
            res = probe_once("https://x", timeout=5, proxy=None)
        self.assertFalse(res["ok"])

    def test_midstream_exit_18_is_drop(self):
        with patch("lib.check_ai.run", return_value=_FakeProc(returncode=18, stdout="",
                                                              stderr="curl: (18) transfer closed")):
            res = probe_once("https://x", timeout=5, proxy=None)
        self.assertFalse(res["ok"])
        self.assertIn("响应中断", res["error"])

    def test_midstream_exit_56_is_drop(self):
        with patch("lib.check_ai.run", return_value=_FakeProc(returncode=56, stdout="",
                                                              stderr="curl: (56) Recv failure")):
            res = probe_once("https://x", timeout=5, proxy=None)
        self.assertFalse(res["ok"])
        self.assertIn("响应中断", res["error"])

    def test_curl_error(self):
        with patch("lib.check_ai.run", return_value=_FakeProc(returncode=7, stdout="",
                                                              stderr="curl: (7) Failed to connect")):
            res = probe_once("https://x", timeout=5, proxy=None)
        self.assertFalse(res["ok"])
        self.assertIn("Failed to connect", res["error"])

    def test_unparsable_output(self):
        with patch("lib.check_ai.run", return_value=_FakeProc(stdout="garbage")):
            res = probe_once("https://x", timeout=5, proxy=None)
        self.assertFalse(res["ok"])
        self.assertIn("无法解析", res["error"])

    def test_proxy_flag_passed_to_curl(self):
        with patch("lib.check_ai.run", return_value=_FakeProc()) as mock_run:
            probe_once("https://x", timeout=5, proxy="http://127.0.0.1:7890")
        cmd = mock_run.call_args.args[0]
        self.assertIn("-x", cmd)
        self.assertIn("http://127.0.0.1:7890", cmd)

    def test_timeout_used_as_curl_m(self):
        with patch("lib.check_ai.run", return_value=_FakeProc()) as mock_run:
            probe_once("https://x", timeout=12, proxy=None)
        cmd = mock_run.call_args.args[0]
        m_idx = cmd.index("-m")
        self.assertEqual(cmd[m_idx + 1], "12")

    def test_no_auth_headers_in_cmd(self):
        with patch("lib.check_ai.run", return_value=_FakeProc()) as mock_run:
            probe_once("https://x", timeout=5, proxy=None)
        cmd = mock_run.call_args.args[0]
        joined = " ".join(cmd)
        self.assertNotIn("authorization", joined.lower())
        self.assertNotIn("x-api-key", joined.lower())


    def test_extra_headers_appended(self):
        with patch("lib.check_ai.run", return_value=_FakeProc()) as mock_run:
            probe_once("https://x", timeout=5, proxy=None, headers={"x-trace": "1"})
        cmd = mock_run.call_args.args[0]
        self.assertIn("x-trace: 1", cmd)

    def test_command_timeout_recorded_as_error(self):
        from lib.exec import CommandTimeout
        with patch("lib.check_ai.run", side_effect=CommandTimeout("curl 超时 20s")):
            res = probe_once("https://x", timeout=15, proxy=None)
        self.assertFalse(res["ok"])
        self.assertIn("超时", res["error"])


class TestMain(unittest.TestCase):
    def test_keyboard_interrupt_summarizes_and_exits_1(self):
        """Ctrl+C 中断 → 打印已完成部分并以 1 退出。"""
        calls = {"n": 0}

        def _probe(*a, **k):
            calls["n"] += 1
            if calls["n"] > 1:
                raise KeyboardInterrupt
            return {"ok": True, "http_code": "401", "ttfb": 0.1,
                    "total": 0.2, "connects": 1, "size": 25, "error": ""}

        with patch("lib.check_ai.probe_once", side_effect=_probe), patch("time.sleep"):
            rc = main(["check_ai", "claude", "-i", "--interval", "0"])
        self.assertEqual(rc, 1)
        self.assertEqual(calls["n"], 2)

    def test_unknown_target_exit_2(self):
        self.assertEqual(main(["check_ai", "no-such-provider"]), 2)

    def test_count_loop_and_exit_code(self):
        with patch("lib.check_ai.probe_once",
                   side_effect=lambda *a, **k: {
                       "ok": True, "http_code": "401", "ttfb": 0.1,
                       "total": 0.2, "connects": 1, "size": 25, "error": ""}), \
             patch("time.sleep"):
            rc = main(["check_ai", "claude", "-n", "3", "--interval", "0"])
        self.assertEqual(rc, 0)

    def test_failure_exit_code_1(self):
        with patch("lib.check_ai.probe_once",
                   side_effect=lambda *a, **k: {
                       "ok": False, "http_code": "000", "ttfb": None,
                       "total": None, "connects": None, "size": None,
                       "error": "响应中断 mid-response drop (curl exit=18)"}), \
             patch("time.sleep"):
            rc = main(["check_ai", "claude", "-n", "2", "--interval", "0"])
        self.assertEqual(rc, 1)

    def test_default_interval_is_5s(self):
        with patch("lib.check_ai.probe_once",
                   side_effect=lambda *a, **k: {
                       "ok": True, "http_code": "401", "ttfb": 0.1,
                       "total": 0.2, "connects": 1, "size": 25, "error": ""}), \
             patch("time.sleep") as mock_sleep:
            main(["check_ai", "claude", "-n", "2"])
        mock_sleep.assert_called_with(5.0)

    def test_no_args_prints_help(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["check_ai"])
        self.assertEqual(rc, 2)
        self.assertIn("-n", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

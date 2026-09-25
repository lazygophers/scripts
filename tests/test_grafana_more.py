#!/usr/bin/env python3
"""lib/grafana.py 剩下没覆盖的分支：URL/参数归一化、鉴权缺失、响应体解码。

已有 tests/test_grafana.py 走的是正常路径；这里只补它没碰到的那些行。
不起真 HTTP：requests 一律 mock。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from lib import grafana as g


class TestNormalizeUrl(unittest.TestCase):
    def test_empty_stays_empty(self):
        self.assertEqual(g.normalize_url(""), "")
        self.assertEqual(g.normalize_url("   "), "")
        self.assertEqual(g.normalize_url(None), "")

    def test_a_bare_host_gets_https(self):
        self.assertTrue(g.normalize_url("grafana.example.test").startswith("https://"))

    def test_an_explicit_scheme_is_kept(self):
        self.assertTrue(g.normalize_url("http://localhost:3000").startswith("http://"))


class TestParseData(unittest.TestCase):
    def test_empty_means_no_body(self):
        self.assertEqual(g.parse_data(None), {})
        self.assertEqual(g.parse_data(""), {})

    def test_a_dict_passes_through(self):
        self.assertEqual(g.parse_data({"a": 1}), {"a": 1})

    def test_json_text_is_parsed(self):
        self.assertEqual(g.parse_data('{"a": 1}'), {"a": 1})

    def test_at_prefix_reads_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "body.json"
            path.write_text('{"from": "file"}', encoding="utf-8")
            self.assertEqual(g.parse_data(f"@{path}"), {"from": "file"})

    def test_a_missing_file_says_which_one(self):
        with self.assertRaises(g.GrafanaError) as caught:
            g.parse_data("@/nowhere/body.json")
        self.assertIn("读不到文件", str(caught.exception))

    def test_broken_json_is_reported_as_such(self):
        with self.assertRaises(g.GrafanaError) as caught:
            g.parse_data("{not json")
        self.assertIn("不是合法 JSON", str(caught.exception))

    def test_a_json_array_is_refused_because_the_body_must_be_an_object(self):
        with self.assertRaises(g.GrafanaError):
            g.parse_data("[1, 2]")

    def test_an_unsupported_type_names_the_type(self):
        with self.assertRaises(g.GrafanaError) as caught:
            g.parse_data(123)
        self.assertIn("int", str(caught.exception))


def client(profile: dict) -> g.GrafanaClient:
    return g.GrafanaClient("grafana.test", {"url": "https://grafana.test", **profile}, {})


class TestUrlBuilding(unittest.TestCase):
    def test_an_absolute_url_is_used_as_is(self):
        self.assertEqual(
            client({"token": "t"})._url("https://other.test/api/health"),
            "https://other.test/api/health",
        )

    def test_a_bare_path_is_assumed_to_live_under_api(self):
        self.assertEqual(client({"token": "t"})._url("health"), "https://grafana.test/api/health")

    def test_a_rooted_path_is_left_alone(self):
        self.assertEqual(client({"token": "t"})._url("/login"), "https://grafana.test/login")


class TestHeaders(unittest.TestCase):
    def test_a_token_becomes_a_bearer_header(self):
        self.assertEqual(client({"token": "t"})._headers()["Authorization"], "Bearer t")

    def test_without_any_credential_it_names_the_command_to_run(self):
        with self.assertRaises(g.GrafanaError) as caught:
            client({})._headers()
        self.assertIn("grafana login", str(caught.exception))

    def test_insecure_turns_certificate_verification_off(self):
        self.assertFalse(client({"token": "t", "insecure": True}).verify)
        self.assertTrue(client({"token": "t"}).verify)


class TestRequestFailures(unittest.TestCase):
    def test_a_transport_error_says_which_call_could_not_connect(self):
        c = client({"token": "t"})
        with patch("requests.request", side_effect=OSError("connection refused")):
            with self.assertRaises(g.GrafanaError) as caught:
                c.get("/api/health")
        message = str(caught.exception)
        self.assertIn("GET", message)
        self.assertIn("连不上", message)


class TestBodyDecoding(unittest.TestCase):
    def test_no_content_decodes_to_none(self):
        resp = MagicMock(status_code=204, content=b"")
        self.assertIsNone(g._body_json(resp))

    def test_an_empty_body_decodes_to_none_even_with_a_200(self):
        resp = MagicMock(status_code=200, content=b"   ")
        self.assertIsNone(g._body_json(resp))

    def test_a_non_json_body_comes_back_as_text(self):
        resp = MagicMock(status_code=200, content=b"plain")
        resp.json.side_effect = ValueError("not json")
        resp.text = "plain"
        self.assertEqual(g._body_json(resp), "plain")

    def test_an_empty_error_body_says_so_instead_of_printing_nothing(self):
        self.assertEqual(g._body_text(MagicMock(text="   ")), "(空响应体)")

    def test_a_long_error_body_is_clipped(self):
        self.assertEqual(len(g._body_text(MagicMock(text="x" * 2000))), 800)


class TestLokiQueryBuilding(unittest.TestCase):
    def test_filters_become_line_matchers_and_blanks_are_skipped(self):
        c = client({"token": "t"})
        captured: dict = {}

        def fake_get(path, **params):
            captured["path"] = path
            captured["params"] = params
            return {"status": "success", "data": {"result": []}}

        with patch.object(c, "get", side_effect=fake_get), \
                patch.object(c, "loki_uid", return_value="loki-1"):
            c.loki_logs('{app="x"}', filters=("boom", "", "panic"))

        self.assertEqual(captured["params"]["query"], '{app="x"} |= `boom` |= `panic`')
        self.assertIn("loki-1", captured["path"])

    def test_a_failed_query_reports_grafana_own_payload(self):
        c = client({"token": "t"})
        with patch.object(c, "get", return_value={"status": "error", "error": "bad selector"}), \
                patch.object(c, "loki_uid", return_value="loki-1"):
            with self.assertRaises(g.GrafanaError) as caught:
                c.loki_logs('{app="x"}')
        self.assertIn("bad selector", str(caught.exception))

    def test_an_explicit_datasource_uid_short_circuits_the_lookup(self):
        c = client({"token": "t"})
        with patch.object(c, "get") as getter:
            self.assertEqual(c.loki_uid("given"), "given")
        getter.assert_not_called()

    def test_without_a_uid_the_first_loki_datasource_wins(self):
        c = client({"token": "t"})
        with patch.object(c, "get", return_value=[
            {"type": "prometheus", "uid": "p"}, {"type": "loki", "uid": "l"},
        ]):
            self.assertEqual(c.loki_uid(), "l")

    def test_no_loki_datasource_names_the_flag_to_use(self):
        c = client({"token": "t"})
        with patch.object(c, "get", return_value=[{"type": "prometheus", "uid": "p"}]):
            with self.assertRaises(g.GrafanaError) as caught:
                c.loki_uid()
        self.assertIn("--datasource", str(caught.exception))


class TestHelpers(unittest.TestCase):
    def test_health_and_search_hit_the_documented_endpoints(self):
        c = client({"token": "t"})
        with patch.object(c, "get", return_value={}) as getter:
            c.health()
            c.search("dash")
        self.assertEqual(getter.call_args_list[0].args[0], "/api/health")
        self.assertEqual(getter.call_args_list[1].args[0], "/api/search")
        self.assertEqual(getter.call_args_list[1].kwargs["query"], "dash")

    def test_post_sends_an_object_body_even_when_none_was_given(self):
        c = client({"token": "t"})
        with patch.object(c, "request", return_value={}) as request:
            c.post("/api/x")
        self.assertEqual(request.call_args.kwargs["json_body"], {})

    def test_the_default_config_path_is_the_module_constant(self):
        self.assertEqual(g.default_config_path(), g.CONFIG_PATH)

    def test_client_for_resolves_a_profile_from_the_config_file(self):
        cfg = {"current": "grafana.test", "profiles": {"grafana.test": {"url": "https://grafana.test", "token": "t"}}}
        with patch.object(g, "load_config", return_value=cfg):
            built = g.client_for()
        self.assertEqual(built.key, "grafana.test")
        self.assertEqual(json.loads(json.dumps(built.profile))["token"], "t")


if __name__ == "__main__":
    unittest.main()

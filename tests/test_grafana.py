"""grafana 客户端测试：配置解析 + token/basic 鉴权 + CLI smoke。"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.grafana import (  # noqa: E402
    GrafanaClient,
    GrafanaError,
    host_key,
    load_config,
    normalize_url,
    parse_data,
    put_profile,
    resolve_profile,
    save_config,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class FakeGrafana(BaseHTTPRequestHandler):
    state: dict = {}

    def log_message(self, *args):
        pass

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw) if raw else {}

    def _authed(self, then):
        st = FakeGrafana.state
        got = self.headers.get("Authorization") or ""
        if st.get("token") and got == f"Bearer {st['token']}":
            return then()
        if st.get("username"):
            raw = f"{st['username']}:{st['password']}".encode()
            if got == "Basic " + base64.b64encode(raw).decode():
                return then()
        return self._json(401, {"message": "Unauthorized"})

    def do_GET(self):
        FakeGrafana.state.setdefault("calls", []).append(("GET", self.path, self.headers.get("Authorization")))
        if self.path == "/api/health":
            return self._authed(lambda: self._json(200, {"database": "ok", "version": "11.0.0"}))
        if self.path.startswith("/api/search?"):
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            return self._authed(lambda: self._json(200, [{"title": params.get("query", [""])[0]}]))
        return self._json(404, {"message": "not found"})

    def do_POST(self):
        FakeGrafana.state.setdefault("calls", []).append(("POST", self.path, self.headers.get("Authorization")))
        body = self._read()
        if self.path == "/api/echo":
            return self._authed(lambda: self._json(200, {"echo": body}))
        return self._json(404, {"message": "not found"})


class ServerCase(unittest.TestCase):
    def setUp(self):
        patcher = unittest.mock.patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})
        patcher.start()
        self.addCleanup(patcher.stop)

        FakeGrafana.state = {"token": "tok", "username": "nico", "password": "pw"}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGrafana)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        host, port = self.server.server_address
        self.url = f"http://{host}:{port}"
        self.key = f"{host}:{port}"
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = pathlib.Path(self.tmp.name) / "grafana.yaml"

    def client(self, **overrides) -> GrafanaClient:
        profile = {"url": self.url, "token": "tok"}
        profile.update(overrides)
        cfg = put_profile({}, self.key, profile)
        return GrafanaClient(self.key, profile, cfg, config_path=self.config_path, timeout=5)


class TestConfigHelpers(unittest.TestCase):
    def test_normalize_url_adds_scheme_and_strips_path(self):
        self.assertEqual(normalize_url("grafana.example.com/d/x"), "https://grafana.example.com")
        self.assertEqual(normalize_url("http://10.0.0.1:3000/"), "http://10.0.0.1:3000")
        self.assertEqual(host_key("HTTPS://Grafana.Example.COM/"), "grafana.example.com")

    def test_normalize_url_rejects_garbage(self):
        with self.assertRaises(GrafanaError):
            normalize_url("https://")

    def test_profile_helpers(self):
        cfg = put_profile({}, "a.com", {"url": "https://a.com"})
        cfg = put_profile(cfg, "b.com", {"url": "https://b.com"})
        self.assertEqual(resolve_profile(cfg, "b.com")[0], "b.com")
        self.assertEqual(resolve_profile(cfg)[0], "a.com")
        with self.assertRaises(GrafanaError):
            resolve_profile({}, "")
        with self.assertRaises(GrafanaError):
            resolve_profile({"profiles": {"a.com": {}, "b.com": {}}}, "")

    def test_parse_data_forms(self):
        self.assertEqual(parse_data(None), {})
        self.assertEqual(parse_data({"a": 1}), {"a": 1})
        self.assertEqual(parse_data('{"a": 1}'), {"a": 1})
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write('{"b": 2}')
            path = f.name
        self.addCleanup(os.unlink, path)
        self.assertEqual(parse_data(f"@{path}"), {"b": 2})
        with self.assertRaises(GrafanaError):
            parse_data("[]")

    def test_save_config_is_0600(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "grafana.yaml"
            save_config({"current": "a.com"}, path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(load_config(path)["current"], "a.com")


class TestClient(ServerCase):
    def test_token_auth_and_query_params(self):
        self.assertEqual(self.client().get("/api/search", query="orders", empty=""), [{"title": "orders"}])
        self.assertIn(("GET", "/api/search?query=orders", "Bearer tok"), FakeGrafana.state["calls"])

    def test_basic_auth_when_no_token(self):
        got = self.client(token="", username="nico", password="pw").get("/api/health")
        self.assertEqual(got["database"], "ok")
        auth = FakeGrafana.state["calls"][-1][2]
        self.assertTrue(auth.startswith("Basic "))

    def test_post_body_round_trip(self):
        self.assertEqual(self.client().post("/api/echo", {"a": 1}), {"echo": {"a": 1}})

    def test_http_error_raises_with_body(self):
        with self.assertRaises(GrafanaError) as ctx:
            self.client(token="wrong").get("/api/health")
        self.assertIn("401", str(ctx.exception))
        self.assertIn("Unauthorized", str(ctx.exception))


class TestCliSmoke(ServerCase):
    def test_hosts_without_config(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, SCRIPTS_NO_SAY="1", PYTHONUSERBASE=str(pathlib.Path.home() / ".local"))
            proc = subprocess.run([str(REPO_ROOT / "bin" / "grafana"), "hosts"],
                                  capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("grafana login", proc.stderr)

    def test_login_then_health_end_to_end(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, SCRIPTS_NO_SAY="1", no_proxy="*", NO_PROXY="*",
                       PYTHONUSERBASE=str(pathlib.Path.home() / ".local"))
            grafana = [str(REPO_ROOT / "bin" / "grafana")]
            login = subprocess.run(grafana + ["login", "--url", self.url, "--token", "tok"],
                                   capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(login.returncode, 0, login.stderr)
            self.assertEqual(load_config(pathlib.Path(home) / ".config" / "lazygophers" / "scripts" / "grafana.yaml")["current"], self.key)

            health = subprocess.run(grafana + ["health"], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(health.returncode, 0, health.stderr)
            self.assertEqual(json.loads(health.stdout)["database"], "ok")

    def test_login_prompts_for_url_when_missing(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, SCRIPTS_NO_SAY="1", no_proxy="*", NO_PROXY="*",
                       PYTHONUSERBASE=str(pathlib.Path.home() / ".local"))
            proc = subprocess.run(
                [str(REPO_ROOT / "bin" / "grafana"), "login"],
                input="http://127.0.0.1:3000\ntok\n",
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Grafana 站点地址", proc.stderr + proc.stdout)
            cfg = load_config(pathlib.Path(home) / ".config" / "lazygophers" / "scripts" / "grafana.yaml")
            self.assertEqual(cfg["profiles"]["127.0.0.1:3000"]["token"], "tok")

    def test_help_uses_compact_colorized_output(self):
        proc = subprocess.run([str(REPO_ROOT / "bin" / "grafana"), "--help"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stderr + proc.stdout
        self.assertIn("Grafana HTTP API 客户端", out)
        self.assertIn("常用", out)
        self.assertIn("login", out)
        self.assertNotIn("help\n", out)


if __name__ == "__main__":
    unittest.main()

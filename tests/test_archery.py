"""archery 客户端测试：配置解析 + JWT 登录 / 续期 / 重试。

HTTP 部分起一个真的本地 ThreadingHTTPServer，模拟 Archery 的 SimpleJWT 端点，
这样 URL 拼接、Authorization 头、401 重试都是端到端跑出来的，不靠 mock 假设。
"""

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

from lib.archery import (  # noqa: E402
    CONFIG_PATH,
    ArcheryClient,
    ArcheryError,
    default_config_path,
    flatten_schema,
    host_key,
    load_config,
    normalize_url,
    parse_data,
    is_root,
    put_profile,
    require_root,
    resolve_profile,
    save_config,
    sudo_argv,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class FakeArchery(BaseHTTPRequestHandler):
    """最小 Archery：/api/auth/token/{,refresh/,verify/} + /api/v1/ping/。

    access token 只认 state["access"]；改这个值就等于服务端让旧 token 过期。
    """

    state: dict = {}

    def log_message(self, *args):  # 静音 stderr 噪音
        pass

    def _json(self, code: int, payload: dict, cookie: str = ""):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw) if raw else {}

    def _read_form(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode() if length else ""
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    def _logged_in(self) -> bool:
        cookie = self.headers.get("Cookie") or ""
        return f"sessionid={FakeArchery.state.get('sessionid')}" in cookie

    def do_POST(self):
        st = FakeArchery.state
        st.setdefault("calls", []).append(("POST", self.path))
        # 网页端：表单 + CSRF + session cookie，跟 JWT 那套完全不同
        if self.path == "/authenticate/":
            form = self._read_form()
            st["web_forms"] = st.setdefault("web_forms", []) + [form]
            if form.get("username") != st["username"] or form.get("password") != st["password"]:
                return self._json(200, {"status": 1, "msg": "用户名或密码错误"})
            if st.get("twofa"):
                return self._json(200, {"status": 0, "msg": "ok", "data": "tmp-session"})
            st["sessionid"] = "web-session"
            return self._json(200, {"status": 0, "msg": "ok", "data": ""},
                              cookie=f"sessionid={st['sessionid']}; Path=/")
        if self.path == "/api/v1/user/2fa/verify/":
            body = self._read()
            from lib.ovpn import totp

            if body.get("otp") != totp(st["totp_secret"]):
                return self._json(200, {"status": 1, "msg": "验证码错误"})
            # 真实 Archery 是把这个临时 session 提升成登录态，cookie 值不变
            st["sessionid"] = "tmp-session"
            return self._json(200, {"status": 0, "msg": "ok"})
        if self.path == "/query/":
            form = self._read_form()
            st["web_forms"] = st.setdefault("web_forms", []) + [form]
            if not self._logged_in():
                return self._html(200, "<html>login</html>")
            return self._json(200, {"status": 0, "msg": "ok",
                                    "data": {"column_list": ["id"], "rows": [[1]]}})

        if self.path == "/query/err/":
            return self._json(200, {"status": 1, "msg": "SQL 语法错误"})

        body = self._read()
        if self.path == "/api/auth/token/":
            if body.get("username") == st["username"] and body.get("password") == st["password"]:
                st["logins"] = st.get("logins", 0) + 1
                st["access"] = f"access-{st['logins']}"
                st["refresh"] = f"refresh-{st['logins']}"
                return self._json(200, {"access": st["access"], "refresh": st["refresh"]})
            return self._json(401, {"detail": "No active account found"})
        if self.path == "/api/auth/token/refresh/":
            if body.get("refresh") == st.get("refresh") and st.get("refresh_ok", True):
                st["access"] = st["access"] + "+"
                return self._json(200, {"access": st["access"]})
            return self._json(401, {"detail": "token_not_valid"})
        if self.path == "/api/auth/token/verify/":
            ok = body.get("token") and body.get("token") == st.get("access")
            return self._json(200 if ok else 401, {} if ok else {"detail": "token_not_valid"})
        if self.path == "/api/v1/sqlquery/execute/":
            return self._authed(lambda: self._json(200, {
                "status": 0, "msg": "ok",
                "data": {"column_list": ["id", "note"], "rows": [[1, "a\tb"], [2, None]]},
            }))
        if self.path == "/api/v1/echo/":
            return self._authed(lambda: self._json(200, {"echo": body}))
        return self._json(404, {"detail": "not found"})

    def _html(self, code: int, text: str):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        FakeArchery.state.setdefault("calls", []).append(("GET", self.path))
        if self.path == "/login/":
            self.send_response(200)
            self.send_header("Set-Cookie", "csrftoken=csrf-1; Path=/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/api/v1/ping/"):
            return self._authed(lambda: self._json(200, {"pong": self.path}))
        if self.path == "/api/boom":
            return self._json(500, {"detail": "炸了"})
        return self._json(404, {"detail": "not found"})

    def do_PUT(self):
        FakeArchery.state.setdefault("calls", []).append(("PUT", self.path))
        body = self._read()
        if self.path == "/api/v1/echo/":
            return self._authed(lambda: self._json(200, {"echo": body}))
        return self._json(404, {"detail": "not found"})

    def do_DELETE(self):
        FakeArchery.state.setdefault("calls", []).append(("DELETE", self.path))
        return self._authed(lambda: (self.send_response(204), self.end_headers()))

    def _authed(self, then):
        expected = f"Bearer {FakeArchery.state.get('access')}"
        if self.headers.get("Authorization") != expected:
            return self._json(401, {"detail": "token_not_valid"})
        return then()


class ServerCase(unittest.TestCase):
    """带 fake 服务端 + 临时配置文件的基类。"""

    def setUp(self):
        # 环境里可能配了 HTTP_PROXY，本地回环必须绕开，否则请求被代理接管
        patcher = unittest.mock.patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})
        patcher.start()
        self.addCleanup(patcher.stop)

        FakeArchery.state = {"username": "nico", "password": "pw"}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeArchery)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        host, port = self.server.server_address
        self.url = f"http://{host}:{port}"
        self.key = f"{host}:{port}"

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = pathlib.Path(self.tmp.name) / "archery.yaml"

    def client(self, **overrides) -> ArcheryClient:
        profile = {"url": self.url, "username": "nico", "password": "pw", "token": {}}
        profile.update(overrides)
        cfg = put_profile({}, self.key, profile)
        return ArcheryClient(self.key, profile, cfg, config_path=self.config_path, timeout=5)


class TestConfigHelpers(unittest.TestCase):
    def test_normalize_url_adds_scheme_and_strips_path(self):
        self.assertEqual(normalize_url("archery.example.com"), "https://archery.example.com")
        self.assertEqual(normalize_url("http://10.0.0.1:9123/x/"), "http://10.0.0.1:9123")
        self.assertEqual(host_key("HTTPS://Archery.Example.COM/"), "archery.example.com")

    def test_normalize_url_rejects_garbage(self):
        with self.assertRaises(ArcheryError):
            normalize_url("https://")

    def test_resolve_profile_prefers_explicit_host(self):
        cfg = {"current": "a.com", "profiles": {"a.com": {"url": "https://a.com"},
                                                "b.com": {"url": "https://b.com"}}}
        self.assertEqual(resolve_profile(cfg, "b.com")[0], "b.com")
        self.assertEqual(resolve_profile(cfg)[0], "a.com")

    def test_resolve_profile_single_needs_no_current(self):
        cfg = {"profiles": {"only.com": {"url": "https://only.com"}}}
        self.assertEqual(resolve_profile(cfg)[0], "only.com")

    def test_resolve_profile_errors(self):
        with self.assertRaises(ArcheryError):
            resolve_profile({})
        multi = {"profiles": {"a.com": {}, "b.com": {}}}
        with self.assertRaises(ArcheryError):
            resolve_profile(multi)
        with self.assertRaises(ArcheryError):
            resolve_profile(multi, "c.com")

    def test_put_profile_sets_current_once(self):
        cfg = put_profile({}, "a.com", {"url": "https://a.com"})
        self.assertEqual(cfg["current"], "a.com")
        cfg = put_profile(cfg, "b.com", {"url": "https://b.com"})
        self.assertEqual(cfg["current"], "a.com")

    def test_parse_data_forms(self):
        self.assertEqual(parse_data(None), {})
        self.assertEqual(parse_data({"a": 1}), {"a": 1})
        self.assertEqual(parse_data('{"a": 1}'), {"a": 1})
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write('{"b": 2}')
            path = f.name
        self.addCleanup(os.unlink, path)
        self.assertEqual(parse_data(f"@{path}"), {"b": 2})
        with self.assertRaises(ArcheryError):
            parse_data("not json")
        with self.assertRaises(ArcheryError):
            parse_data("[1,2]")

    def test_normalize_url_empty_is_empty(self):
        self.assertEqual(normalize_url(""), "")
        self.assertEqual(normalize_url("   "), "")

    def test_parse_data_missing_file(self):
        with self.assertRaises(ArcheryError):
            parse_data("@/definitely/not/here.json")

    def test_parse_data_rejects_other_types(self):
        with self.assertRaises(ArcheryError):
            parse_data(123)

    def test_mask(self):
        from lib.archery import mask
        self.assertEqual(mask(""), "(未设置)")
        self.assertEqual(mask("abcd"), "****")
        self.assertEqual(mask("abcdef"), "ab**ef")

    def test_flatten_schema_skips_non_dict_ops(self):
        schema = {"paths": {"/a/": "junk", "/b/": {"get": {"summary": "ok"}}}}
        self.assertEqual(flatten_schema(schema), [("GET", "/b/", "ok")])

    def test_client_for_picks_current_profile(self):
        from lib.archery import client_for
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "archery.yaml"
            save_config(put_profile({}, "a.com", {"url": "https://a.com"}), path)
            c = client_for(config_path=path)
            self.assertEqual(c.key, "a.com")

    def test_flatten_schema(self):
        schema = {"paths": {"/api/v1/user/": {"get": {"summary": "用户清单"},
                                              "post": {"summary": "创建用户"},
                                              "parameters": []}}}
        self.assertEqual(sorted(flatten_schema(schema)), [
            ("GET", "/api/v1/user/", "用户清单"),
            ("POST", "/api/v1/user/", "创建用户"),
        ])

    def test_save_config_is_0600(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "archery.yaml"
            save_config({"current": "a.com"}, path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(load_config(path)["current"], "a.com")


class TestRootGate(unittest.TestCase):
    """密钥类命令的 root 门槛：非 root 时用 sudo 重跑自己。"""

    def test_sudo_argv_appends_config_and_absolute_python(self):
        cmd = sudo_argv(pathlib.Path("/x/bin/archery"), ["show", "--host", "a.com"],
                        pathlib.Path("/home/nico/archery.yaml"))
        self.assertEqual(cmd, ["sudo", sys.executable, "/x/bin/archery", "show",
                               "--host", "a.com", "--config", "/home/nico/archery.yaml"])

    def test_sudo_argv_keeps_explicit_config(self):
        cmd = sudo_argv(pathlib.Path("/x/bin/archery"), ["code", "--config", "/tmp/a.yaml"],
                        pathlib.Path("/home/nico/archery.yaml"))
        self.assertEqual(cmd.count("--config"), 1)
        self.assertNotIn("/home/nico/archery.yaml", cmd)

    def test_require_root_execs_sudo_when_not_root(self):
        with unittest.mock.patch("lib.archery.is_root", return_value=False), \
                unittest.mock.patch("lib.archery.os.execvp") as execvp:
            require_root(pathlib.Path("/x/bin/archery"), ["code"], pathlib.Path("/c.yaml"))
        execvp.assert_called_once()
        self.assertEqual(execvp.call_args[0][0], "sudo")
        self.assertIn("--config", execvp.call_args[0][1])

    def test_require_root_is_noop_when_root(self):
        with unittest.mock.patch("lib.archery.is_root", return_value=True), \
                unittest.mock.patch("lib.archery.os.execvp") as execvp:
            require_root(pathlib.Path("/x/bin/archery"), ["code"], pathlib.Path("/c.yaml"))
        execvp.assert_not_called()

    def test_require_root_reports_missing_sudo(self):
        with unittest.mock.patch("lib.archery.is_root", return_value=False), \
                unittest.mock.patch("lib.archery.os.execvp", side_effect=OSError("no sudo")):
            with self.assertRaises(ArcheryError) as ctx:
                require_root(pathlib.Path("/x/bin/archery"), ["code"], pathlib.Path("/c.yaml"))
        self.assertIn("sudo", str(ctx.exception))

    def test_default_config_path_plain_user(self):
        with unittest.mock.patch("lib.archery.is_root", return_value=False):
            self.assertEqual(default_config_path(), CONFIG_PATH)

    def test_default_config_path_falls_back_to_sudo_user_home(self):
        """sudo 把 HOME 换成 /var/root 时，配置得从发起 sudo 的那个用户家目录读。"""
        with tempfile.TemporaryDirectory() as home:
            target = pathlib.Path(home) / ".config" / "lazygophers" / "scripts" / "archery.yaml"
            target.parent.mkdir(parents=True)
            target.write_text("current: a.com\n", encoding="utf-8")
            fake_pw = unittest.mock.Mock(pw_dir=home)
            with unittest.mock.patch("lib.archery.is_root", return_value=True), \
                    unittest.mock.patch.dict(os.environ, {"SUDO_USER": "nico"}), \
                    unittest.mock.patch("pwd.getpwnam", return_value=fake_pw):
                self.assertEqual(default_config_path(), target)

    def test_default_config_path_ignores_unknown_sudo_user(self):
        with unittest.mock.patch("lib.archery.is_root", return_value=True), \
                unittest.mock.patch.dict(os.environ, {"SUDO_USER": "ghost"}), \
                unittest.mock.patch("pwd.getpwnam", side_effect=KeyError("ghost")):
            self.assertEqual(default_config_path(), CONFIG_PATH)

    def test_is_root_matches_euid(self):
        self.assertEqual(is_root(), os.geteuid() == 0)


class TestClient(ServerCase):
    def test_login_stores_tokens_in_config_file(self):
        client = self.client()
        client.login()
        saved = load_config(self.config_path)["profiles"][self.key]["token"]
        self.assertEqual(saved["access"], "access-1")
        self.assertEqual(saved["refresh"], "refresh-1")

    def test_login_failure_message_carries_body(self):
        client = self.client(password="wrong")
        with self.assertRaises(ArcheryError) as ctx:
            client.login()
        self.assertIn("401", str(ctx.exception))
        self.assertFalse(self.config_path.exists())

    def test_request_logs_in_lazily_and_sends_bearer(self):
        client = self.client()
        self.assertEqual(client.get("v1/ping/"), {"pong": "/api/v1/ping/"})
        self.assertEqual(FakeArchery.state["logins"], 1)

    def test_path_forms_resolve_to_same_url(self):
        client = self.client()
        base = self.url
        self.assertEqual(client._url("v1/ping/"), f"{base}/api/v1/ping/")
        self.assertEqual(client._url("/api/v1/ping/"), f"{base}/api/v1/ping/")
        self.assertEqual(client._url(f"{base}/api/v1/ping/"), f"{base}/api/v1/ping/")

    def test_401_triggers_refresh_then_retry(self):
        client = self.client()
        client.login()
        FakeArchery.state["access"] = "server-rotated"  # 手里的 access 作废
        self.assertEqual(client.get("v1/ping/"), {"pong": "/api/v1/ping/"})
        self.assertEqual(FakeArchery.state["logins"], 1)  # 只 refresh，没重新登录
        self.assertEqual(load_config(self.config_path)["profiles"][self.key]["token"]["access"],
                         FakeArchery.state["access"])

    def test_dead_refresh_falls_back_to_password_login(self):
        client = self.client()
        client.login()
        FakeArchery.state["access"] = "server-rotated"
        FakeArchery.state["refresh_ok"] = False
        self.assertEqual(client.get("v1/ping/"), {"pong": "/api/v1/ping/"})
        self.assertEqual(FakeArchery.state["logins"], 2)

    def test_query_params_drop_empty_values(self):
        client = self.client()
        client.get("v1/ping/", size=50, search="", missing=None)
        self.assertIn(("GET", "/api/v1/ping/?size=50"), FakeArchery.state["calls"])

    def test_post_body_round_trip(self):
        client = self.client()
        self.assertEqual(client.post("v1/echo/", {"a": 1}), {"echo": {"a": 1}})

    def test_delete_returns_none_on_204(self):
        client = self.client()
        self.assertIsNone(client.delete("v1/user/7/"))

    def test_http_error_raises_with_body(self):
        client = self.client()
        with self.assertRaises(ArcheryError) as ctx:
            client.request("GET", "/api/boom", auth=False)
        self.assertIn("500", str(ctx.exception))
        self.assertIn("炸了", str(ctx.exception))

    def test_unreachable_host_raises_archery_error(self):
        client = self.client(url="http://127.0.0.1:1")
        with self.assertRaises(ArcheryError) as ctx:
            client.request("GET", "/api/info", auth=False)
        self.assertIn("连不上", str(ctx.exception))

    def test_verify_token(self):
        client = self.client()
        self.assertFalse(client.verify_token())

    def test_verify_token_true_after_login(self):
        client = self.client()
        client.login()
        self.assertTrue(client.verify_token())

    def test_put_sends_json_body(self):
        client = self.client()
        self.assertEqual(client.put("v1/echo/", {"a": 2}), {"echo": {"a": 2}})

    def test_login_without_credentials_errors(self):
        client = self.client(username="", password="")
        with self.assertRaises(ArcheryError) as ctx:
            client.login()
        self.assertIn("缺用户名或密码", str(ctx.exception))

    def test_login_response_without_access_errors(self):
        client = self.client()
        fake = unittest.mock.MagicMock(status_code=200)
        fake.json.return_value = {"refresh": "r"}
        with unittest.mock.patch.object(client, "_post_json", return_value=fake):
            with self.assertRaises(ArcheryError) as ctx:
                client.login()
        self.assertIn("没有 access token", str(ctx.exception))


class TestWebFallback(ServerCase):
    """Archery 1.9.x 没有 sqlquery API 时，查询走网页端 session 的回退路径。"""

    def web_client(self, **overrides) -> ArcheryClient:
        FakeArchery.state["totp_secret"] = "JBSWY3DPEHPK3PXP"
        return self.client(totp_secret="JBSWY3DPEHPK3PXP", **overrides)

    def test_web_login_without_2fa(self):
        client = self.web_client()
        got = client.web("POST", "/query/", form={"sql_content": "select 1"})
        self.assertEqual(got["data"]["rows"], [[1]])
        self.assertNotIn(("POST", "/api/v1/user/2fa/verify/"), FakeArchery.state["calls"])

    def test_web_login_passes_2fa_then_queries(self):
        FakeArchery.state["twofa"] = True
        client = self.web_client()
        got = client.web("POST", "/query/", form={"sql_content": "select 1"})
        self.assertEqual(got["data"]["column_list"], ["id"])
        self.assertIn(("POST", "/api/v1/user/2fa/verify/"), FakeArchery.state["calls"])

    def test_web_form_keeps_empty_values(self):
        """空串字段必须照发：网页视图会把缺的字段写进库，少发一个就 500。"""
        client = self.web_client()
        client.web("POST", "/query/", form={"sql_content": "select 1", "alias": "", "x": None})
        form = FakeArchery.state["web_forms"][-1]
        self.assertEqual(form.get("alias"), "")
        self.assertNotIn("x", form)

    def test_web_relogins_when_session_expired(self):
        client = self.web_client()
        client.web("POST", "/query/", form={"sql_content": "select 1"})
        FakeArchery.state["sessionid"] = "rotated-by-server"  # 等于服务端踢掉登录态
        got = client.web("POST", "/query/", form={"sql_content": "select 1"})
        self.assertEqual(got["status"], 0)

    def test_web_cookies_are_persisted_and_reused(self):
        """cookie 落盘：第二个进程（这里换个 client）直接拿去用，不再登录。"""
        FakeArchery.state["twofa"] = True
        first = self.web_client()
        first.web("POST", "/query/", form={"sql_content": "select 1"})
        saved = load_config(self.config_path)["profiles"][self.key]["web_cookies"]
        self.assertEqual(saved["sessionid"], "tmp-session")

        second = ArcheryClient(self.key, load_config(self.config_path)["profiles"][self.key],
                               {}, config_path=self.config_path, timeout=5)
        FakeArchery.state["calls"] = []
        second.web("POST", "/query/", form={"sql_content": "select 1"})
        self.assertNotIn(("POST", "/authenticate/"), FakeArchery.state["calls"])

    def test_stale_cookies_trigger_one_relogin(self):
        """服务端不认存着的 cookie 时清掉重登，且新 cookie 覆盖写回配置。"""
        client = self.web_client()
        client.web("POST", "/query/", form={"sql_content": "select 1"})
        FakeArchery.state["sessionid"] = "rotated-by-server"

        fresh = ArcheryClient(self.key, load_config(self.config_path)["profiles"][self.key],
                              {}, config_path=self.config_path, timeout=5)
        FakeArchery.state["calls"] = []
        got = fresh.web("POST", "/query/", form={"sql_content": "select 1"})
        self.assertEqual(got["status"], 0)
        self.assertIn(("POST", "/authenticate/"), FakeArchery.state["calls"])
        saved = load_config(self.config_path)["profiles"][self.key]["web_cookies"]
        self.assertEqual(saved["sessionid"], "web-session")

    def test_concurrent_writers_keep_each_others_fields(self):
        """两个进程各写一半：一个存 token，一个存 cookie，谁都不该被覆盖掉。"""
        a = self.web_client()
        b = ArcheryClient(self.key, dict(a.profile), {},
                          config_path=self.config_path, timeout=5)
        a.login()                                              # A 写 token
        b.web("POST", "/query/", form={"sql_content": "select 1"})  # B 写 cookie
        disk = load_config(self.config_path)["profiles"][self.key]
        self.assertTrue(disk["token"]["access"])
        self.assertTrue(disk["web_cookies"]["sessionid"])

    def test_parallel_writes_never_corrupt_the_file(self):
        """十个写者同时刷 token：配置文件任何时刻都是完整的，账号密码不丢。"""
        clients = [ArcheryClient(self.key, {"url": self.url, "username": "nico",
                                            "password": "pw", "token": {}},
                                 {}, config_path=self.config_path, timeout=5)
                   for _ in range(10)]
        errors: list[Exception] = []

        def hammer(c, i):
            try:
                for _ in range(5):
                    c._store_token(f"access-{i}")
                    self.assertEqual(load_config(self.config_path)["profiles"][self.key]["password"], "pw")
            except Exception as e:  # noqa: BLE001 — 线程里的异常要带回主线程断言
                errors.append(e)

        threads = [threading.Thread(target=hammer, args=(c, i)) for i, c in enumerate(clients)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        disk = load_config(self.config_path)["profiles"][self.key]
        self.assertTrue(disk["token"]["access"].startswith("access-"))

    def test_web_login_failure_message(self):
        client = self.web_client(password="wrong")
        with self.assertRaises(ArcheryError) as ctx:
            client.web("POST", "/query/", form={})
        self.assertIn("网页端登录失败", str(ctx.exception))

    def test_web_login_without_credentials_errors(self):
        client = self.web_client(username="", password="")
        with self.assertRaises(ArcheryError) as ctx:
            client.web("POST", "/query/", form={})
        self.assertIn("缺用户名或密码", str(ctx.exception))

    def test_2fa_wrong_code_reports_server_message(self):
        FakeArchery.state["twofa"] = True
        self.web_client()  # 先让服务端持有正确密钥
        client = self.client(totp_secret="JBSWY3DPEHPK3PXQ")  # 客户端拿的是另一个
        with self.assertRaises(ArcheryError) as ctx:
            client.web("POST", "/query/", form={})
        self.assertIn("2FA 校验失败", str(ctx.exception))
        self.assertIn("验证码错误", str(ctx.exception))

    def test_web_error_status_raises_with_message(self):
        client = self.web_client()
        with self.assertRaises(ArcheryError) as ctx:
            client.web("POST", "/query/err/", form={})
        self.assertIn("SQL 语法错误", str(ctx.exception))

    def test_web_html_twice_gives_up(self):
        """登录动作没生效时（页面一直重定向回登录页），重试一次后报错。"""
        client = self.web_client()
        with unittest.mock.patch.object(ArcheryClient, "web_login", lambda self: None):
            with self.assertRaises(ArcheryError) as ctx:
                client.web("POST", "/query/", form={})
        self.assertIn("返回的不是 JSON", str(ctx.exception))

    def test_web_needs_totp_secret_when_2fa_on(self):
        FakeArchery.state["twofa"] = True
        client = self.client()  # 没配 totp_secret
        with self.assertRaises(ArcheryError) as ctx:
            client.web("POST", "/query/", form={})
        self.assertIn("totp_secret", str(ctx.exception))


class TestCliSmoke(unittest.TestCase):
    """bin/archery 黑盒：没有配置时给出下一步动作，退出码非 0。"""

    def test_bare_command_shows_skills(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, SCRIPTS_NO_SAY="1", PYTHONUSERBASE=str(pathlib.Path.home() / ".local"))
            proc = subprocess.run([str(REPO_ROOT / "bin" / "archery")], capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("# archery skills", proc.stderr + proc.stdout)
            self.assertIn("archery query execute", proc.stderr + proc.stdout)

    def test_login_then_api_end_to_end(self):
        """login 写配置 → api 用配置里的凭据取 token 并发请求，全程非交互。"""
        FakeArchery.state = {"username": "nico", "password": "pw"}
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeArchery)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        host, port = server.server_address
        url = f"http://{host}:{port}"

        with tempfile.TemporaryDirectory() as home:
            # 只改 HOME 会把 user site-packages（requests 装在那）一起搬走，
            # 所以显式把 PYTHONUSERBASE 钉回真实家目录。
            env = dict(os.environ, HOME=home, SCRIPTS_NO_SAY="1", no_proxy="*", NO_PROXY="*",
                       PYTHONUSERBASE=str(pathlib.Path.home() / ".local"))
            archery = [sys.executable, str(REPO_ROOT / "bin" / "archery")]

            login = subprocess.run(
                archery + ["login", "--url", url, "--username", "nico", "--password", "pw"],
                capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(login.returncode, 0, login.stderr)

            cfg = load_config(pathlib.Path(home) / ".config" / "lazygophers"
                              / "scripts" / "archery.yaml")
            self.assertEqual(cfg["current"], f"{host}:{port}")

            ping = subprocess.run(archery + ["api", "get", "v1/ping/"],
                                  capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(ping.returncode, 0, ping.stderr)
            self.assertEqual(json.loads(ping.stdout), {"pong": "/api/v1/ping/"})

    def test_query_execute_prints_tsv_by_default(self):
        """默认输出 TSV：首行列名，值里的制表符转义，NULL 变空。"""
        FakeArchery.state = {"username": "nico", "password": "pw"}
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeArchery)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        host, port = server.server_address

        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, SCRIPTS_NO_SAY="1", no_proxy="*", NO_PROXY="*",
                       PYTHONUSERBASE=str(pathlib.Path.home() / ".local"))
            archery = [sys.executable, str(REPO_ROOT / "bin" / "archery")]
            subprocess.run(archery + ["login", "--url", f"http://{host}:{port}",
                                      "--username", "nico", "--password", "pw"],
                           capture_output=True, text=True, env=env, timeout=60)

            run = subprocess.run(
                archery + ["query", "execute", "select 1",
                           "--instance-name", "ins", "--db-name", "db"],
                capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.splitlines(),
                             ["id\tnote", "1\ta\\tb", "2\t"])

            as_table = subprocess.run(
                archery + ["query", "execute", "select 1",
                           "--instance-name", "ins", "--db-name", "db", "--table"],
                capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(as_table.returncode, 0, as_table.stderr)
            self.assertIn("│", as_table.stdout)

    @unittest.skipIf(os.geteuid() == 0, "已经是 root，不会走 sudo 重跑")
    def test_show_reexecs_through_sudo(self):
        """非 root 跑 `show`：真的走 execvp("sudo", ...)，用假 sudo 截下来看参数。"""
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as bindir:
            fake_sudo = pathlib.Path(bindir) / "sudo"
            fake_sudo.write_text('#!/bin/sh\necho "FAKE-SUDO $@"\n', encoding="utf-8")
            fake_sudo.chmod(0o755)
            env = dict(os.environ, HOME=home, SCRIPTS_NO_SAY="1",
                       PATH=f"{bindir}:{os.environ.get('PATH', '')}",
                       PYTHONUSERBASE=str(pathlib.Path.home() / ".local"))
            proc = subprocess.run(
                [sys.executable, str(REPO_ROOT / "bin" / "archery"), "show", "--host", "a.com"],
                capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("FAKE-SUDO", proc.stdout)
            self.assertIn("--config", proc.stdout)
            self.assertIn(str(pathlib.Path(home) / ".config"), proc.stdout)

    def test_help_lists_all_groups(self):
        proc = subprocess.run([sys.executable, str(REPO_ROOT / "bin" / "archery"), "--", "--help"],
                              capture_output=True, text=True, timeout=60)
        for name in ("user", "instance", "query", "workflow", "schema", "api"):
            self.assertIn(name, proc.stderr + proc.stdout)


if __name__ == "__main__":
    unittest.main()

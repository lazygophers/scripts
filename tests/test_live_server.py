"""tests for lib/live_server.py — 单主缝：in-process 真实 HTTP（spec 测试决策）"""
import base64
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from unittest import mock

from lib.live_server import (Config, inside_root, make_server,
                             render_listing, render_markdown_page)

class ServerCase(unittest.TestCase):
    """每个用例在随机端口起真实服务，用 urllib 打 HTTP。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cfg = Config(root=self.dir)
        self.srv = make_server(self.cfg, lambda m: None)
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    # ---- HTTP 助手 ----
    def _req(self, path, method="GET", data=None, auth=None):
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if auth:
            req.add_header("Authorization", "Basic " + base64.b64encode(
                auth.encode()).decode())
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as _e:
            return _e.code, _e.read()

    def get(self, path, **kw):
        return self._req(path, **kw)

    def put(self, path, data, **kw):
        return self._req(path, method="PUT", data=data, **kw)

    # ---- 用例 ----
    def test_index_fallback_and_listing(self):
        (self.dir / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"<h1>hi</h1>", body)

        (self.dir / "index.html").unlink()
        (self.dir / "a.txt").write_text("x" * 2048, encoding="utf-8")
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"a.txt", body)
        self.assertIn(b"2.0 KB", body)

    def test_listing_subdir(self):
        (self.dir / "sub").mkdir()
        (self.dir / "sub" / "b.md").write_text("# t", encoding="utf-8")
        code, body = self.get("/sub/")
        self.assertEqual(code, 200)
        self.assertIn(b"b.md", body)
        self.assertIn(b"crumbs", body)

    def test_spa_fallback(self):
        (self.dir / "index.html").write_text("<h1>spa</h1>", encoding="utf-8")
        self.cfg.spa = True
        code, body = self.get("/some/route")  # 无扩展名 → 回退
        self.assertEqual(code, 200)
        self.assertIn(b"spa", body)
        code, _ = self.get("/nope.js")        # 有扩展名 → 404 照旧
        self.assertEqual(code, 404)

    def test_spa_off_404(self):
        code, _ = self.get("/nothing/here")
        self.assertEqual(code, 404)

    def test_traversal_403(self):
        code, _ = self.get("/../etc/passwd")
        self.assertEqual(code, 403)

    def test_put_roundtrip_and_traversal(self):
        code, _ = self.put("/up/hello.txt", b"hello")
        self.assertEqual(code, 201)
        self.assertEqual((self.dir / "up" / "hello.txt").read_bytes(), b"hello")
        code, _ = self.put("/hello.txt", b"v1")
        self.assertEqual(code, 201)
        code, _ = self.put("/hello.txt", b"v2")
        self.assertEqual(code, 200)  # 同名覆盖
        for evil in ["/../evil.txt", "/a/../../evil.txt", "/%2e%2e/evil.txt"]:
            code, _ = self.put(evil, b"x")
            self.assertEqual(code, 403, evil)

    def test_auth(self):
        self.cfg.auth = ("u", "p")
        code, body = self.get("/", auth="u:p")
        self.assertEqual(code, 200)
        self.assertEqual(self.get("/")[0], 401)
        self.assertEqual(self.get("/", auth="u:wrong")[0], 401)

    def test_md_wrapped(self):
        (self.dir / "r.md").write_text("# t", encoding="utf-8")
        code, body = self.get("/r.md")
        self.assertEqual(code, 200)
        self.assertIn(b"marked.min.js", body)

    def test_asset_unknown_404(self):
        code, _ = self.get("/_assets/unknown.js")
        self.assertIn(code, (403, 404))

    def test_sse_hub_semantics(self):
        code, _ = self.put("/w.txt", b"2")
        self.assertEqual(code, 201)
        evs = self.cfg.hub.after(self.cfg.hub.seq - 1, 1.0)
        self.assertTrue(evs)
        self.assertEqual(evs[-1][1], "w.txt")

class PureFuncTest(unittest.TestCase):
    def test_inside_root(self):
        base = Path(tempfile.mkdtemp())
        self.assertTrue(inside_root(base, base / "a" / "b"))
        self.assertTrue(inside_root(base, base))
        # 同级相似名不算在内（baseevil != base）
        evil = base.parent / (base.name + "evil")
        evil.mkdir()
        self.assertFalse(inside_root(base, evil))

    def test_templates(self):
        self.assertIn("marked.min.js", render_markdown_page("t"))
        html = render_listing("docs", [
            {"name": "x.txt", "is_dir": False, "mtime": "09-29 10:00",
             "size": "1 B", "href": "/x.txt"}], "/docs")
        self.assertIn("x.txt", html)
        self.assertIn("crumbs", html)

if __name__ == "__main__":
    unittest.main()


class TestAssetsAndCert(unittest.TestCase):
    """fetch_asset 的四条出路 + ensure_cert 的三个来源。"""

    def setUp(self):
        import lib.live_server as ls
        self.ls = ls
        self.tmp = Path(tempfile.mkdtemp())
        self.msgs = []
        self.ran = ""

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unknown_name_and_all_urls_fail(self):
        self.assertIsNone(self.ls.fetch_asset("no-such-asset"))
        with mock.patch.object(self.ls, "CDN_CACHE_ROOT", self.tmp), \
             mock.patch("urllib.request.urlopen",
                                 side_effect=OSError("offline")):
            name = next(iter(self.ls.CDN_ASSETS))
            self.assertIsNone(self.ls.fetch_asset(name, timeout=0.1))

    def test_cached_and_download_paths(self):
        import io
        name = next(iter(self.ls.CDN_ASSETS))
        with mock.patch.object(self.ls, "CDN_CACHE_ROOT", self.tmp):
            dest = self.tmp / name
            dest.write_bytes(b"CACHED")
            self.assertEqual(self.ls.fetch_asset(name), dest)

            dest.unlink()
            fake = mock.Mock()
            fake.__enter__ = lambda s: io.BytesIO(b"DOWNLOADED")
            fake.__exit__ = lambda s, *a: False
            with mock.patch("urllib.request.urlopen", return_value=fake), \
                 mock.patch.object(self.ls, "is_debug", return_value=True):
                got = self.ls.fetch_asset(name)
            self.assertEqual(got.read_bytes(), b"DOWNLOADED")
            self.assertFalse((self.tmp / (name + ".part")).exists())

    def test_ensure_cert_sources(self):
        import os
        cfg = self.ls.Config(root=self.tmp, cert=self.tmp / "c.pem",
                             key=self.tmp / "k.pem")
        self.assertEqual(self.ls.ensure_cert(cfg, lambda m: None),
                         (self.tmp / "c.pem", self.tmp / "k.pem"))

        with mock.patch.object(self.ls, "CONFIG_DIR", self.tmp / "cfg"):
            # 已有证书：直接复用
            (self.tmp / "cfg").mkdir()
            (self.tmp / "cfg/cert.pem").write_bytes(b"")
            (self.tmp / "cfg/key.pem").write_bytes(b"")
            self.assertEqual(
                self.ls.ensure_cert(self.ls.Config(root=self.tmp), lambda m: None),
                (self.tmp / "cfg/cert.pem", self.tmp / "cfg/key.pem"))

            # openssl 自签：子进程桩掉（真 openssl 在 conda 环境可能没有配置文件），
            # 桩负责落一对假证书；权限收 0600 这一步还是要验真的
            (self.tmp / "cfg/cert.pem").unlink()
            (self.tmp / "cfg/key.pem").unlink()

            def fake_run(cmd, check=None):
                key_path = Path(cmd[cmd.index("-keyout") + 1])
                cert_path = Path(cmd[cmd.index("-out") + 1])
                key_path.write_bytes(b"k")
                cert_path.write_bytes(b"c")
                self.ran = cmd[0]
                return mock.DEFAULT

            with mock.patch("shutil.which",
                            lambda b: "/usr/bin/openssl" if b == "openssl" else None), \
                 mock.patch("subprocess.run", side_effect=fake_run):
                cert, key = self.ls.ensure_cert(self.ls.Config(root=self.tmp),
                                                lambda m: self.msgs.append(m))
                self.assertTrue(cert.is_file())
                self.assertEqual(oct(os.stat(cert).st_mode)[-3:], "600")
                self.assertTrue(any("openssl" in m for m in self.msgs))
                self.assertEqual(self.ran, "openssl")

            # 什么都没有：报错不糊弄
            (self.tmp / "cfg/cert.pem").unlink()
            (self.tmp / "cfg/key.pem").unlink()
            with mock.patch("shutil.which", lambda b: None):
                with self.assertRaises(RuntimeError):
                    self.ls.ensure_cert(self.ls.Config(root=self.tmp), lambda m: None)


    def test_handler_asset_branches(self):
        # 静态资产端点：已知但离线 → 503
        case = ServerCase()
        case.setUp()
        try:
            with mock.patch("lib.live_server.fetch_asset", return_value=None):
                code, body = case.get("/_assets/" + next(iter(self.ls.CDN_ASSETS)))
            self.assertEqual(code, 503)
            self.assertIn(b"offline", body)
            code, body = case.get("/_assets/nope.js")
            self.assertEqual(code, 404)
        finally:
            case.tearDown()

    def test_put_into_dir_403_and_delete_405(self):
        case = ServerCase()
        case.setUp()
        try:
            (case.cfg.root / "d").mkdir()
            code, _ = case.put("/d", b"x")
            self.assertEqual(code, 403)
            code, _ = case._req("/f.txt", method="DELETE")
            self.assertEqual(code, 405)
        finally:
            case.tearDown()

    def test_dir_index_served_and_favicon(self):
        case = ServerCase()
        case.setUp()
        try:
            (case.cfg.root / "sub").mkdir()
            (case.cfg.root / "sub/index.html").write_text("<h1>idx</h1>")
            code, body = case.get("/sub/")
            self.assertEqual(code, 200)
            self.assertIn(b"idx", body)
            code, body = case.get("/favicon.ico")
            self.assertEqual(code, 200)
            self.assertTrue(body.startswith(b"<svg"))
        finally:
            case.tearDown()


class TestWatcher(unittest.TestCase):
    def test_start_watcher_publishes_events(self):
        import lib.live_server as ls
        d = Path(tempfile.mkdtemp()).resolve()  # /var → /private/var 软链归一
        hub = ls.ChangeHub()
        obs = ls.start_watcher(d, hub)
        self.assertIsNotNone(obs)
        try:
            (d / "f.txt").write_text("x")
            deadline = time.time() + 5
            seen = []
            while time.time() < deadline:
                seen = list(hub.after(0, 0.05))
                if seen:
                    break
                time.sleep(0.05)
            self.assertTrue(any("f.txt" in p for _, p, _ in seen), seen)
        finally:
            obs.stop()
            obs.join(timeout=2)
            import shutil
            shutil.rmtree(d, ignore_errors=True)


class TestSseAndRun(unittest.TestCase):
    """SSE 流的真实往返 + run() 主循环（模拟 Ctrl-C）。"""

    def test_sse_stream_pushes_events_then_ping(self):
        import lib.live_server as ls
        case = ServerCase()
        case.setUp()
        try:
            req = urllib.request.Request(case.base + "/__changes/stream")
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(r.headers["Content-Type"], "text/event-stream")
                # 连接建立后再发布（SSE 从连接时的 hub.seq 往后发）
                timer = threading.Timer(0.3, case.cfg.hub.publish,
                                        ["late.txt", "modify"])
                timer.start()
                got = b""
                while b"event: change" not in got:
                    chunk = r.readline()
                    if not chunk:
                        break
                    got += chunk
                timer.cancel()
                got += r.readline()  # data: {...} 在 event: 行的下一行
            # ping 在 hub.after 的 15s 等窗之后才发，这里不等它；事件到了就关连接，
            # 服务端走 BrokenPipeError 收场——那正是 SSE 循环的退出路径
            self.assertIn(b"late.txt", got)
        finally:
            case.tearDown()

    def test_run_serves_and_shuts_down_on_keyboard_interrupt(self):
        import lib.live_server as ls
        import threading

        d = Path(tempfile.mkdtemp())
        msgs: list = []
        cfg = ls.Config(root=d)

        class Stopper(threading.Thread):
            # serve_forever 起来后从别的线程抛 KeyboardInterrupt 进不去，
            # 用 shutdown() 模拟 Ctrl-C 后 finally 走清理、返回 0
            def run(self):
                deadline = time.time() + 5
                while time.time() < deadline and not any("/" in m for m in msgs):
                    time.sleep(0.02)
                srv_ref[0].shutdown()

        srv_ref = [None]
        real_make = ls.make_server

        def make_and_keep(c, say):
            srv_ref[0] = real_make(c, say)
            return srv_ref[0]

        with mock.patch.object(ls, "make_server", make_and_keep), \
             mock.patch.object(ls, "start_watcher", return_value=None):
            Stopper().start()
            rc = ls.run(cfg, lambda m: msgs.append(m))
        self.assertEqual(rc, 0)
        self.assertTrue(any("http://127.0.0.1" in m for m in msgs))
        self.assertTrue(any("watchdog" in m for m in msgs))
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    def test_run_with_watcher_and_browser_and_tls_flags(self):
        import lib.live_server as ls
        import threading

        d = Path(tempfile.mkdtemp())
        msgs: list = []
        opened: list = []
        cert, key = self._tiny_cert(d)
        cfg = ls.Config(root=d, tls=True, open_browser=True,
                        cert=cert, key=key)
        srv_ref = [None]
        real_make = ls.make_server

        class Stopper(threading.Thread):
            def run(self):
                deadline = time.time() + 5
                while time.time() < deadline and srv_ref[0] is None:
                    time.sleep(0.02)
                if srv_ref[0] is not None:
                    srv_ref[0].shutdown()

        def make_and_keep(c, say):
            srv_ref[0] = real_make(c, say)
            return srv_ref[0]

        fake_obs = mock.Mock()
        with mock.patch.object(ls, "make_server", make_and_keep), \
             mock.patch.object(ls, "start_watcher", return_value=fake_obs), \
             mock.patch("webbrowser.open", opened.append):
            Stopper().start()
            rc = ls.run(cfg, lambda m: msgs.append(m))
        self.assertEqual(rc, 0)
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].startswith("https://"))
        fake_obs.stop.assert_called()
        self.assertTrue(any(m.startswith("https://") for m in msgs))
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    @staticmethod
    def _tiny_cert(d: Path) -> tuple:
        import subprocess
        out = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-days", "1", "-subj", "/CN=localhost",
             "-keyout", str(d / "k.pem"), "-out", str(d / "c.pem")],
            capture_output=True)
        if out.returncode != 0:
            raise unittest.SkipTest(f"openssl 不可用: {out.stderr[:200]}")
        return d / "c.pem", d / "k.pem"


class TestWatcherEdges(unittest.TestCase):
    def test_no_watchdog_returns_none(self):
        import lib.live_server as ls
        import sys
        with mock.patch.dict(sys.modules, {"watchdog.events": None,
                                          "watchdog.observers": None}):
            self.assertIsNone(ls.start_watcher(Path("."), ls.ChangeHub()))

    def test_dir_events_skipped_and_on_change_called(self):
        import lib.live_server as ls
        d = Path(tempfile.mkdtemp()).resolve()
        hub = ls.ChangeHub()
        changed: list = []
        obs = ls.start_watcher(d, hub, on_change=changed.append)
        self.assertIsNotNone(obs)
        try:
            (d / "subdir").mkdir()          # 目录事件：跳过
            (d / "g.txt").write_text("x")   # 文件事件：发布 + on_change
            deadline = time.time() + 5
            while time.time() < deadline and not changed:
                time.sleep(0.05)
            self.assertTrue(changed and set(changed) == {"g.txt"},
                            f"目录事件不该进 on_change: {changed}")
            events = [p for _, p, _ in hub.after(0, 0.05)]
            self.assertIn("g.txt", events)
            self.assertNotIn("subdir", events)
        finally:
            obs.stop()
            obs.join(timeout=2)
            import shutil
            shutil.rmtree(d, ignore_errors=True)


class TestAssetServing(unittest.TestCase):
    def test_asset_served_from_cache(self):
        import lib.live_server as ls
        case = ServerCase()
        case.setUp()
        try:
            name = next(iter(ls.CDN_ASSETS))
            cached = Path(tempfile.mkdtemp()) / name
            cached.write_bytes(b"ASSET-BODY")
            with mock.patch("lib.live_server.fetch_asset", return_value=cached):
                code, body = case.get(f"/_assets/{name}")
            self.assertEqual(code, 200)
            self.assertEqual(body, b"ASSET-BODY")
        finally:
            case.tearDown()

    def test_mkcert_branch(self):
        import lib.live_server as ls
        import os
        d = Path(tempfile.mkdtemp())
        cfgdir = d / "cfg"
        cfgdir.mkdir()

        def fake_run(cmd, check=None):
            cmd[cmd.index("-cert-file") + 1]
            Path(cmd[cmd.index("-key-file") + 1]).write_bytes(b"k")
            Path(cmd[cmd.index("-cert-file") + 1]).write_bytes(b"c")
            return mock.DEFAULT

        with mock.patch.object(ls, "CONFIG_DIR", cfgdir), \
             mock.patch("shutil.which", lambda b: "/usr/local/bin/mkcert" if b == "mkcert" else None), \
             mock.patch("subprocess.run", side_effect=fake_run):
            cert, key = ls.ensure_cert(ls.Config(root=d), lambda m: None)
        self.assertTrue(cert.is_file())
        self.assertEqual(oct(os.stat(key).st_mode)[-3:], "600")

        import shutil
        shutil.rmtree(d, ignore_errors=True)

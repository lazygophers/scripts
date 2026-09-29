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

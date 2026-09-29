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
                             render_listing, render_map_page,
                             render_markdown_page)


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

    def test_map_mode(self):
        gj = self.dir / "pts.geojson"
        gj.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{"type": "Feature",
                          "properties": {"n": "x"},
                          "geometry": {"type": "Point",
                                       "coordinates": [116.4, 39.9]}}]}),
                         encoding="utf-8")
        self.cfg.map_file = gj
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"ol.js", body)
        code, body = self.get("/data.geojson")
        self.assertEqual(200, code)
        self.assertEqual(
            json.loads(body)["features"][0]["properties"]["n"], "x")

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
        self.assertIn("ol.js", render_map_page("t"))
        html = render_listing("docs", [
            {"name": "x.txt", "is_dir": False, "mtime": "09-29 10:00",
             "size": "1 B", "href": "/x.txt"}], "/docs")
        self.assertIn("x.txt", html)
        self.assertIn("crumbs", html)


if __name__ == "__main__":
    unittest.main()


class GeojsonLintTest(unittest.TestCase):
    """RFC 7946 校验（lib.geojson_errors 纯函数）"""

    def test_valid_feature_collection(self):
        from lib.live_server import geojson_errors

        good = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"n": 1},
             "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}]}
        self.assertEqual(geojson_errors(good), [])

    def test_missing_type_is_reported(self):
        from lib.live_server import geojson_errors

        errs = geojson_errors([{"features": []}])  # 裸数组，OL 报 undefined
        self.assertTrue(any("JSON 对象" in e for e in errs))

    def test_string_coordinates(self):
        from lib.live_server import geojson_errors

        bad = {"type": "Point", "coordinates": ["116.4", "39.9"]}
        errs = geojson_errors(bad)
        self.assertTrue(any("位置" in e for e in errs))

    def test_unclosed_ring(self):
        from lib.live_server import geojson_errors

        bad = {"type": "Polygon", "coordinates": [
            [[0, 0], [1, 0], [1, 1], [0, 1]]]}
        errs = geojson_errors(bad)
        self.assertTrue(any("闭合" in e for e in errs))

    def test_null_geometry_and_crs_warn(self):
        from lib.live_server import geojson_errors

        ok = {"type": "Feature", "geometry": None, "properties": None}
        self.assertEqual(geojson_errors(ok), [])
        errs = geojson_errors({"type": "FeatureCollection", "crs": "EPSG:3857",
                               "features": []})
        self.assertTrue(any("crs" in e for e in errs))

    def test_bad_feature_type(self):
        from lib.live_server import geojson_errors

        errs = geojson_errors({"type": "FeatureCollection", "features": [
            {"type": "feature", "geometry": None, "properties": {}}]})
        self.assertTrue(any('"Feature"' in e for e in errs))


class CheckCliTest(unittest.TestCase):
    def test_check_cli_exit_codes(self):
        import subprocess
        import sys

        from lib.ai_env import is_ai_shell_env  # noqa: F401

        tmp = Path(tempfile.mkdtemp())
        good = tmp / "good.geojson"
        good.write_text('{"type":"Point","coordinates":[1,2]}', encoding="utf-8")
        rc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, '.');"
             "from lib.cli.live_server import main;"
             "sys.argv=['live_server','map','check',%r];"
             "raise SystemExit(main(sys.argv))" % str(good)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(rc.returncode, 0, rc.stderr)

        bad = tmp / "bad.geojson"
        bad.write_text('[{"x":1}]', encoding="utf-8")
        rc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, '.');"
             "from lib.cli.live_server import main;"
             "sys.argv=['live_server','map','check',%r];"
             "raise SystemExit(main(sys.argv))" % str(bad)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(rc.returncode, 1)
        self.assertIn("ERROR:", rc.stderr)


class LeafletTest(ServerCase):
    def test_leaflet_template_and_assets_whitelist(self):
        from lib.live_server import CDN_ASSETS, render_map_page

        html = render_map_page("t", lib="leaflet")
        self.assertIn("leaflet.js", html)
        self.assertIn("L.geoJSON", html)
        self.assertNotIn("ol.js", html)
        html = render_map_page("t", lib="ol")
        self.assertIn("ol.js", html)
        self.assertIn("leaflet.js", CDN_ASSETS)
        self.assertIn("leaflet.css", CDN_ASSETS)

    def test_map_mode_leaflet(self):
        gj = self.dir / "p.geojson"
        gj.write_text('{"type":"Point","coordinates":[116.4,39.9]}',
                      encoding="utf-8")
        self.cfg.map_file = gj
        self.cfg.render_lib = "leaflet"
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"leaflet.js", body)
        self.assertNotIn(b"ol.js", body)


class NormalizeTest(unittest.TestCase):
    def test_normalize_geojson(self):
        from lib.live_server import normalize_geojson

        # 裸要素数组 → FeatureCollection
        norm = normalize_geojson([{"geometry": None, "properties": {}}])
        self.assertEqual(norm["type"], "FeatureCollection")
        # 裸几何 → Feature
        norm = normalize_geojson({"type": "Point", "coordinates": [1, 2]})
        self.assertEqual(norm["type"], "Feature")
        self.assertEqual(norm["geometry"]["type"], "Point")
        # 规范文件原样
        good = {"type": "FeatureCollection", "features": []}
        self.assertIs(normalize_geojson(good), good)

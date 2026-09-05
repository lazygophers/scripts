"""webgrab 单元测试（mock 网络层）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import webgrab


class TestIsBlocked(unittest.TestCase):
    def test_blocked_status(self):
        self.assertTrue(webgrab.is_blocked(403, "<html>ok</html>"))

    def test_ok_response(self):
        self.assertFalse(webgrab.is_blocked(200, "<html>hello world</html>"))

    def test_cf_marker_in_body(self):
        self.assertTrue(webgrab.is_blocked(200, "<title>Just a moment...</title>"))

    def test_marker_beyond_head_ignored(self):
        self.assertFalse(webgrab.is_blocked(200, "x" * 20001 + "just a moment"))


class TestGrab(unittest.TestCase):
    def test_first_impersonate_passes(self):
        with patch.object(webgrab, "fetch_direct", return_value=(200, "<html>hi</html>")):
            html, src = webgrab.grab("https://a.com")
        self.assertEqual(html, "<html>hi</html>")
        self.assertIn("chrome", src)

    def test_rotation_then_fallback_render(self):
        direct = patch.object(webgrab, "fetch_direct", return_value=(403, "Just a moment"))
        render = patch.object(webgrab, "fetch_render", return_value="<html>rendered</html>")
        with direct, render:
            html, src = webgrab.grab("https://a.com")
        self.assertEqual(html, "<html>rendered</html>")
        self.assertEqual(src, "playwright 渲染")

    def test_render_still_blocked_raises(self):
        direct = patch.object(webgrab, "fetch_direct", return_value=(403, "nope"))
        render = patch.object(webgrab, "fetch_render", return_value="Verify you are human")
        with direct, render:
            with self.assertRaises(webgrab.GrabError):
                webgrab.grab("https://a.com")

    def test_force_render_skips_direct(self):
        with patch.object(webgrab, "fetch_direct") as d, \
             patch.object(webgrab, "fetch_render", return_value="<html>x</html>"):
            webgrab.grab("https://a.com", force_render=True)
        d.assert_not_called()


class TestCli(unittest.TestCase):
    def test_writes_md_to_output(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(webgrab, "fetch_direct", return_value=(200, "<h1>标题</h1>")):
            rc = webgrab.main(["webgrab", "https://example.com/page", "-o", str(Path(td) / "out.md")])
            written = (Path(td) / "out.md").read_text()
        self.assertEqual(rc, 0)
        self.assertEqual(written, "# 标题\n")

    def test_html_flag_keeps_raw(self):
        import io
        buf = io.StringIO()
        with patch.object(webgrab, "fetch_direct", return_value=(200, "<h1>x</h1>")), \
             patch.object(sys, "stdout", buf):
            rc = webgrab.main(["webgrab", "https://example.com", "--html"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue(), "<h1>x</h1>")

    def test_stdout_mode(self):
        import io
        buf = io.StringIO()
        with patch.object(webgrab, "fetch_direct", return_value=(200, "CONTENT")), \
             patch.object(sys, "stdout", buf):
            rc = webgrab.main(["webgrab", "https://example.com"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue(), "CONTENT\n")

    def test_grab_error_returns_1(self):
        direct = patch.object(webgrab, "fetch_direct", return_value=(403, "nope"))
        render = patch.object(webgrab, "fetch_render", return_value="Verify you are human")
        with direct, render:
            rc = webgrab.main(["webgrab", "https://example.com"])
        self.assertEqual(rc, 1)


class TestDefaultOutput(unittest.TestCase):
    def test_domain_name(self):
        self.assertEqual(webgrab.default_output("https://a.b.com/x?y=1", "md"), Path("a.b.com.md"))

    def test_no_host_fallback(self):
        self.assertEqual(webgrab.default_output("not-a-url", "html"), Path("page.html"))

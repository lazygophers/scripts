#!/usr/bin/env python3
"""Tests for lib.ui (强制 Rich，无降级路径)。"""
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import lib.ui as ui_mod


def _buf_reporter():
    """Reporter 写入 StringIO，便于捕获 Rich 输出。"""
    buf = io.StringIO()
    r = ui_mod.Reporter(file=buf)
    return r, buf


class TestModuleConstants(unittest.TestCase):
    def test_icons_defined(self):
        self.assertEqual(ui_mod.ICON_SUCCESS, "✓")
        self.assertEqual(ui_mod.ICON_ERROR, "✗")
        self.assertEqual(ui_mod.ICON_STEP, "→")

    def test_has_rich_is_true(self):
        # 强制 Rich：HAS_RICH 必须为 True，否则启动时直接 raise
        self.assertTrue(ui_mod.HAS_RICH)

    def test_styles_non_none(self):
        for s in (ui_mod.STYLE_SUCCESS, ui_mod.STYLE_ERROR, ui_mod.STYLE_WARNING,
                  ui_mod.STYLE_INFO, ui_mod.STYLE_STEP, ui_mod.STYLE_DIM):
            self.assertIsNotNone(s)


class TestReporterRich(unittest.TestCase):
    def test_info(self):
        r, buf = _buf_reporter()
        r.info("hello")
        out = buf.getvalue()
        self.assertIn("hello", out)
        self.assertIn(ui_mod.ICON_INFO, out)

    def test_step(self):
        r, buf = _buf_reporter()
        r.step("doing")
        out = buf.getvalue()
        self.assertIn("doing", out)
        self.assertIn(ui_mod.ICON_STEP, out)

    def test_ok(self):
        r, buf = _buf_reporter()
        r.ok("done")
        out = buf.getvalue()
        self.assertIn("done", out)
        self.assertIn(ui_mod.ICON_SUCCESS, out)

    def test_warn(self):
        r, buf = _buf_reporter()
        r.warn("careful")
        out = buf.getvalue()
        self.assertIn("careful", out)
        self.assertIn(ui_mod.ICON_WARNING, out)

    def test_err(self):
        r, buf = _buf_reporter()
        r.err("bad")
        out = buf.getvalue()
        self.assertIn("bad", out)
        self.assertIn(ui_mod.ICON_ERROR, out)

    def test_rule(self):
        r, buf = _buf_reporter()
        r.rule("Title")
        out = buf.getvalue()
        self.assertIn("Title", out)

    def test_kv(self):
        r, buf = _buf_reporter()
        r.kv("Meta", {"a": "1", "b": "2"})
        out = buf.getvalue()
        self.assertIn("a", out)
        self.assertIn("1", out)
        self.assertIn("b", out)

    def test_kv_empty(self):
        r, buf = _buf_reporter()
        r.kv("Empty", {})
        self.assertIn("Empty", buf.getvalue())

    def test_output_truncates(self):
        r, buf = _buf_reporter()
        long_text = "\n".join(f"line{i}" for i in range(50))
        r.output(long_text, max_lines=10)
        out = buf.getvalue()
        self.assertIn("line0", out)
        self.assertIn("...", out)

    def test_output_empty(self):
        r, buf = _buf_reporter()
        r.output("")
        self.assertEqual(buf.getvalue(), "")

    def test_summary(self):
        r, buf = _buf_reporter()
        r.summary("Result", [("成功", "3", "green"), ("失败", "0", None)])
        out = buf.getvalue()
        self.assertIn("成功", out)
        self.assertIn("3", out)

    def test_panel(self):
        r, buf = _buf_reporter()
        r.panel("P", "line1\nline2")
        out = buf.getvalue()
        self.assertIn("P", out)
        self.assertIn("line1", out)


class TestReporterFactory(unittest.TestCase):
    def test_reporter_returns_instance(self):
        r = ui_mod.reporter(stderr=False)
        self.assertIsInstance(r, ui_mod.Reporter)
        self.assertFalse(r.stderr)


class TestConsoleProgress(unittest.TestCase):
    def test_console_returns_console(self):
        self.assertIsNotNone(ui_mod.console())

    def test_progress_none_console_raises(self):
        with self.assertRaises(ValueError):
            ui_mod.progress(None)


class TestStatusMethods(unittest.TestCase):
    def test_status_picks_icon_per_status(self):
        r, buf = _buf_reporter()
        r.status("ok", "done")
        r.status("skip", "later")
        r.status("fail", "boom")
        out = buf.getvalue()
        self.assertIn(ui_mod.ICON_SUCCESS, out)
        self.assertIn(ui_mod.ICON_SKIP, out)
        self.assertIn(ui_mod.ICON_ERROR, out)
        self.assertIn("done", out)
        self.assertIn("boom", out)

    def test_status_table_renders_labels_and_details(self):
        r, buf = _buf_reporter()
        r.status_table("执行结果", [
            ("repoA", "ok", ""),
            ("repoB", "skip", "已对齐"),
            ("repoC", "fail", "fetch 失败"),
        ])
        out = buf.getvalue()
        self.assertIn("执行结果", out)
        self.assertIn("repoA", out)
        self.assertIn("成功", out)
        self.assertIn("跳过", out)
        self.assertIn("失败", out)
        self.assertIn("fetch 失败", out)

    def test_status_footer_joins_with_dot(self):
        r, buf = _buf_reporter()
        r.status_footer([("失败 1/3", "red"), ("成功 1/3", "green")])
        out = buf.getvalue()
        self.assertIn("失败 1/3", out)
        self.assertIn("成功 1/3", out)
        self.assertIn("·", out)

    def test_status_footer_empty_noop(self):
        r, buf = _buf_reporter()
        r.status_footer([])
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
#!/usr/bin/env python3
"""Tests for lib.ui (纯文本降级路径)."""
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import lib.ui as ui_mod


def _plain_reporter():
    """构造一个强制纯文本 (无 Rich) 的 Reporter。"""
    r = ui_mod.Reporter(stderr=False)
    r.console = None  # 强制走纯文本分支
    return r


class TestModuleConstants(unittest.TestCase):
    def test_icons_defined(self):
        self.assertEqual(ui_mod.ICON_SUCCESS, "✓")
        self.assertEqual(ui_mod.ICON_ERROR, "✗")
        self.assertEqual(ui_mod.ICON_STEP, "→")

    def test_has_rich_is_bool(self):
        self.assertIn(ui_mod.HAS_RICH, (True, False))


class TestReporterPlainText(unittest.TestCase):
    def test_info(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.info("hello")
        self.assertIn("hello", buf.getvalue())
        self.assertIn(ui_mod.ICON_INFO, buf.getvalue())

    def test_step(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.step("doing")
        self.assertIn("doing", buf.getvalue())
        self.assertIn(ui_mod.ICON_STEP, buf.getvalue())

    def test_ok(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.ok("done")
        self.assertIn("done", buf.getvalue())
        self.assertIn(ui_mod.ICON_SUCCESS, buf.getvalue())

    def test_warn(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.warn("careful")
        self.assertIn("careful", buf.getvalue())
        self.assertIn(ui_mod.ICON_WARNING, buf.getvalue())

    def test_err(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.err("bad")
        self.assertIn("bad", buf.getvalue())
        self.assertIn(ui_mod.ICON_ERROR, buf.getvalue())

    def test_rule(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.rule("Title")
        out = buf.getvalue()
        self.assertIn("Title", out)
        self.assertIn("═", out)

    def test_kv(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.kv("Meta", {"a": "1", "b": "2"})
        out = buf.getvalue()
        self.assertIn("a", out)
        self.assertIn("1", out)
        self.assertIn("b", out)

    def test_kv_empty(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.kv("Empty", {})
        # 空 dict 不应崩
        self.assertIn("Empty", buf.getvalue())

    def test_output_truncates(self):
        r = _plain_reporter()
        long_text = "\n".join(f"line{i}" for i in range(50))
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.output(long_text, max_lines=10)
        out = buf.getvalue()
        self.assertIn("line0", out)
        self.assertIn("...", out)

    def test_output_empty(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.output("")
        self.assertEqual(buf.getvalue(), "")

    def test_summary(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.summary("Result", [("成功", "3", "green"), ("失败", "0", None)])
        out = buf.getvalue()
        self.assertIn("成功", out)
        self.assertIn("3", out)

    def test_cmd_result_success(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.cmd_result(["echo", "x"], returncode=0, show_output=True, output="x")
        out = buf.getvalue()
        self.assertIn("echo", out)

    def test_cmd_result_failure(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.cmd_result(["make"], returncode=2, show_output=True, output="error here")
        out = buf.getvalue()
        self.assertIn("exit=2", out)
        self.assertIn("error here", out)

    def test_panel(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.panel("P", "line1\nline2")
        out = buf.getvalue()
        self.assertIn("P", out)
        self.assertIn("line1", out)


class TestReporterStderr(unittest.TestCase):
    def test_stderr_flag_routes_to_stderr(self):
        r = ui_mod.Reporter(stderr=True)
        r.console = None
        buf = io.StringIO()
        with redirect_stderr(buf):
            r.info("to-stderr")
        self.assertIn("to-stderr", buf.getvalue())


class TestReporterFactory(unittest.TestCase):
    def test_reporter_returns_instance(self):
        r = ui_mod.reporter(stderr=False)
        self.assertIsInstance(r, ui_mod.Reporter)
        self.assertFalse(r.stderr)


class TestConsoleProgress(unittest.TestCase):
    def test_console_no_rich_returns_none(self):
        with patch.object(ui_mod, "HAS_RICH", False):
            self.assertIsNone(ui_mod.console())

    def test_progress_none_console_returns_none(self):
        self.assertIsNone(ui_mod.progress(None))


class TestStatusMethods(unittest.TestCase):
    """status / status_table / status_footer（纯文本降级路径）。"""

    def test_status_picks_icon_per_status(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.status("ok", "done")
            r.status("skip", "later")
            r.status("fail", "boom")
        out = buf.getvalue()
        self.assertIn(ui_mod.ICON_SUCCESS, out)  # ✓
        self.assertIn(ui_mod.ICON_SKIP, out)      # •
        self.assertIn(ui_mod.ICON_ERROR, out)     # ✗
        self.assertIn("done", out)
        self.assertIn("boom", out)

    def test_status_table_renders_labels_and_details(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.status_table("执行结果", [
                ("repoA", "ok", ""),
                ("repoB", "skip", "已对齐"),
                ("repoC", "fail", "fetch 失败"),
            ])
        out = buf.getvalue()
        self.assertIn("执行结果", out)
        self.assertIn("repoA", out)
        self.assertIn("repoB", out)
        self.assertIn("repoC", out)
        self.assertIn("成功", out)
        self.assertIn("跳过", out)
        self.assertIn("失败", out)
        self.assertIn("fetch 失败", out)

    def test_status_footer_joins_with_dot(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.status_footer([("失败 1/3", "red"), ("成功 1/3", "green")])
        out = buf.getvalue()
        self.assertIn("失败 1/3", out)
        self.assertIn("成功 1/3", out)
        self.assertIn("·", out)

    def test_status_footer_empty_noop(self):
        r = _plain_reporter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.status_footer([])
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()

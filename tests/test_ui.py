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


class TestPrintAnsiAndEprint(unittest.TestCase):
    def test_print_ansi_none_console_raises(self):
        with self.assertRaises(ValueError):
            ui_mod.print_ansi(None, "x")

    def test_print_ansi_strips_escape_codes(self):
        buf = io.StringIO()
        con = ui_mod.Console(file=buf)
        ui_mod.print_ansi(con, "\x1b[31mred\x1b[0m")
        self.assertIn("red", buf.getvalue())

    def test_eprint_writes_stderr(self):
        buf = io.StringIO()
        with patch.object(sys, "stderr", buf):
            ui_mod._eprint("boom")
        self.assertEqual(buf.getvalue(), "boom\n")


class TestReporterConstruction(unittest.TestCase):
    def test_explicit_console_is_reused(self):
        buf = io.StringIO()
        con = ui_mod.Console(file=buf)
        r = ui_mod.Reporter(console=con)
        self.assertIs(r.console, con)
        r.info("hi")
        self.assertIn("hi", buf.getvalue())

    def test_default_uses_stderr_console(self):
        r = ui_mod.Reporter(stderr=True)
        self.assertTrue(r.console.stderr)

    def test_from_buffer(self):
        buf = io.StringIO()
        r = ui_mod.Reporter.from_buffer(buf)
        r.ok("saved")
        self.assertIn("saved", buf.getvalue())

    def test_private_print_ignores_plain_text(self):
        r, buf = _buf_reporter()
        r._print(ui_mod.Text("rich-only"), "plain-fallback")
        out = buf.getvalue()
        self.assertIn("rich-only", out)
        self.assertNotIn("plain-fallback", out)


class TestCmdResult(unittest.TestCase):
    def test_success_uses_step(self):
        r, buf = _buf_reporter()
        r.cmd_result(["git", "status"], returncode=0)
        out = buf.getvalue()
        self.assertIn("git status", out)
        self.assertIn(ui_mod.ICON_STEP, out)

    def test_returncode_none_uses_step(self):
        r, buf = _buf_reporter()
        r.cmd_result(["ls"])
        self.assertIn(ui_mod.ICON_STEP, buf.getvalue())

    def test_failure_shows_exit_code(self):
        r, buf = _buf_reporter()
        r.cmd_result(["git", "push"], returncode=128)
        out = buf.getvalue()
        self.assertIn("exit=128", out)
        self.assertIn(ui_mod.ICON_ERROR, out)

    def test_title_and_cwd_in_head(self):
        r, buf = _buf_reporter()
        r.cmd_result(["ls"], cwd="/tmp/x", title="列目录")
        out = buf.getvalue().replace("\n", "")
        self.assertIn("列目录", out)
        self.assertIn("/tmp/x", out)

    def test_show_output_prints_body(self):
        r, buf = _buf_reporter()
        r.cmd_result(["ls"], returncode=0, output="a.txt\nb.txt", show_output=True)
        out = buf.getvalue()
        self.assertIn("a.txt", out)
        self.assertIn("b.txt", out)

    def test_show_output_skips_blank(self):
        r, buf = _buf_reporter()
        r.cmd_result(["ls"], returncode=0, output="   \n ", show_output=True)
        self.assertNotIn("  \n  \n", buf.getvalue())


class TestAskHelpers(unittest.TestCase):
    def test_ask_confirm_returns_answer(self):
        with patch("rich.prompt.Confirm.ask", return_value=True) as m:
            self.assertTrue(ui_mod.ask_confirm("go?", default=False))
        self.assertEqual(m.call_args.kwargs["default"], False)

    def test_ask_confirm_eof_returns_none(self):
        with patch("rich.prompt.Confirm.ask", side_effect=EOFError):
            self.assertIsNone(ui_mod.ask_confirm("go?"))

    def test_ask_confirm_interrupt_returns_none(self):
        with patch("rich.prompt.Confirm.ask", side_effect=KeyboardInterrupt):
            self.assertIsNone(ui_mod.ask_confirm("go?"))

    def test_ask_text_returns_answer(self):
        with patch("rich.prompt.Prompt.ask", return_value="nico"):
            self.assertEqual(ui_mod.ask_text("name", default="x"), "nico")

    def test_ask_text_eof_returns_none(self):
        with patch("rich.prompt.Prompt.ask", side_effect=EOFError):
            self.assertIsNone(ui_mod.ask_text("name"))

    def test_ask_text_interrupt_returns_none(self):
        with patch("rich.prompt.Prompt.ask", side_effect=KeyboardInterrupt):
            self.assertIsNone(ui_mod.ask_text("name"))


class TestFormatElapsed(unittest.TestCase):
    def test_sub_millisecond(self):
        self.assertEqual(ui_mod._format_elapsed(0.0001), "<1ms")

    def test_milliseconds(self):
        self.assertEqual(ui_mod._format_elapsed(0.823), "823ms")

    def test_seconds(self):
        self.assertEqual(ui_mod._format_elapsed(12.34), "12.3s")

    def test_minutes(self):
        self.assertEqual(ui_mod._format_elapsed(83), "1m23s")

    def test_hours(self):
        self.assertEqual(ui_mod._format_elapsed(3723), "1h2m3s")


class TestPrintRuntime(unittest.TestCase):
    def test_prints_elapsed_and_label(self):
        buf = io.StringIO()
        real_console = ui_mod.Console
        with patch.object(ui_mod, "Console", lambda **kw: real_console(file=buf)):
            ui_mod.print_runtime(1_700_000_000.0, 1_700_000_012.5, label="merge")
        out = buf.getvalue()
        self.assertIn("⏱", out)
        self.assertIn("merge", out)
        self.assertIn("12.5s", out)
        self.assertIn("–", out)

    def test_no_label(self):
        buf = io.StringIO()
        real_console = ui_mod.Console
        with patch.object(ui_mod, "Console", lambda **kw: real_console(file=buf)):
            ui_mod.print_runtime(1_700_000_000.0, 1_700_000_000.5)
        out = buf.getvalue()
        self.assertIn("⏱", out)
        self.assertIn("500ms", out)


class TestTimed(unittest.TestCase):
    def test_returns_value_and_prints(self):
        calls = []
        with patch.object(ui_mod, "print_runtime", lambda *a, **k: calls.append(k)):
            wrapped = ui_mod.timed(lambda a, b: a + b, label="sum")
            self.assertEqual(wrapped(1, b=2), 3)
        self.assertEqual(calls, [{"label": "sum"}])

    def test_prints_on_exception(self):
        calls = []
        with patch.object(ui_mod, "print_runtime", lambda *a, **k: calls.append(k)):
            def boom():
                raise ValueError("x")
            with self.assertRaises(ValueError):
                ui_mod.timed(boom)()
        self.assertEqual(len(calls), 1)

    def test_preserves_name(self):
        def named(argv):
            return 0
        self.assertEqual(ui_mod.timed(named).__name__, "named")


if __name__ == "__main__":
    unittest.main()
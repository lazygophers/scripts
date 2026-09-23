"""Reporter 极简输出模式（AI shell 环境自动启用）。"""
import io
import os
import unittest
from unittest.mock import patch

from lib.ui import Reporter


def make_reporter(env):
    with patch.dict(os.environ, env, clear=False):
        return Reporter.from_buffer(io.StringIO())


class TestMinimalOutput(unittest.TestCase):
    def test_claude_code_env_activates_minimal(self):
        r = make_reporter({"CLAUDECODE": "1"})
        self.assertTrue(r.minimal)
        self.assertTrue(r.console.no_color)

    def test_plain_env_stays_rich(self):
        r = make_reporter({"TERM": "xterm-256color"})
        self.assertFalse(r.minimal)

    def test_info_and_step_dropped(self):
        r = make_reporter({"CLAUDECODE": "1"})
        r.info("提示信息")
        r.step("执行中")
        self.assertEqual(r.console.file.getvalue(), "")

    def test_ok_is_silent_and_err_is_kept_plain(self):
        r = make_reporter({"CLAUDECODE": "1"})
        r.ok("合并成功")
        r.err("合并失败")
        out = r.console.file.getvalue()
        self.assertNotIn("合并成功", out)
        self.assertIn("ERROR: 合并失败", out)
        self.assertNotIn("✓", out)  # 无图标
        self.assertNotIn("✗", out)

    def test_kv_no_box(self):
        r = make_reporter({"CLAUDECODE": "1"})
        r.kv("详情", {"分支": "master", "远端": "origin"})
        out = r.console.file.getvalue()
        self.assertIn("分支: master", out)
        self.assertIn("远端: origin", out)
        self.assertNotIn("╭", out)  # 无边框

    def test_rule_and_panel(self):
        r = make_reporter({"CLAUDECODE": "1"})
        r.rule("开始")
        self.assertEqual(r.console.file.getvalue(), "")  # rule 直接丢弃
        r.panel("摘要", "第一行\n第二行")
        out = r.console.file.getvalue()
        self.assertIn("摘要:", out)
        self.assertIn("第一行", out)


if __name__ == "__main__":
    unittest.main()


class TestMinimalOtherChannels(unittest.TestCase):
    def test_runtime_line_plain(self):
        import contextlib
        import io

        import lib.ui as ui_mod
        buf = io.StringIO()
        with patch.dict(os.environ, {"CLAUDECODE": "1"}):
            with contextlib.redirect_stderr(buf):
                ui_mod.print_runtime(1_700_000_000.0, 1_700_000_012.5,
                                     label="merge", elapsed=12.5)
        out = buf.getvalue()
        self.assertIn("merge: 12.5s", out)
        self.assertNotIn("⏱", out)
        self.assertNotIn("–", out)  # 无起止时间段

    def test_progress_disabled(self):
        from rich.console import Console

        import lib.ui as ui_mod
        with patch.dict(os.environ, {"CLAUDECODE": "1"}):
            p = ui_mod.progress(Console(file=io.StringIO()))
        self.assertTrue(p.disable)

    def test_browse_table_tsv_or_json(self):
        from lib.cli.browse import print_result

        buf = io.StringIO()
        with patch.dict(os.environ, {"CLAUDECODE": "1"}):
            # 拍得平的（list-of-dicts）降级 TSV
            print_result({"tabs": [{"title": "a"}, {"title": "b"}]}, table=True, out=buf)
            # 拍不平的保持压缩 JSON
            print_result({"nested": {"a": [1, 2]}}, table=True, out=buf)
        lines = buf.getvalue().splitlines()
        self.assertEqual(lines[0], "title")
        self.assertEqual(lines[1], "a")
        self.assertEqual(lines[2], "b")
        self.assertEqual(lines[3], '{"nested":{"a":[1,2]}}')
        self.assertNotIn("╭", buf.getvalue())

    def test_archery_table_forced_tsv(self):
        import inspect

        from lib.cli import archery
        src = inspect.getsource(archery)
        self.assertIn("table and not is_ai_shell_env()", src)

    def test_print_tsv_escapes(self):
        import io as _io

        from lib.ui import print_tsv

        buf = _io.StringIO()
        print_tsv(["a", "b"], [["x\ty", "z\nw"]], file=buf)
        self.assertEqual(buf.getvalue(), "a\tb\nx\\ty\tz\\nw\n")

    def test_websearch_default_tsv_in_ai_env(self):
        import contextlib
        import io as _io

        import lib.websearch as ws
        from unittest.mock import patch as _patch

        with _patch.object(ws, "search", return_value=[{"url": "u", "title": "t", "snippet": "s"}]):
            buf = _io.StringIO()
            with patch.dict(os.environ, {"CLAUDECODE": "1"}):
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(_io.StringIO()):
                    rc = ws.main(["websearch", "query"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue(), "index\turl\ttitle\tsnippet\n1\tu\tt\ts\n")

    def test_fire_help_no_forced_ansi(self):
        import inspect

        from lib import fire_base
        src = inspect.getsource(fire_base._render_fire_help)
        self.assertIn("force_terminal=not is_ai_shell_env()", src)

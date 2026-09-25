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

    def test_status_table_keeps_status_hides_nonfailure_details(self):
        r = make_reporter({"CLAUDECODE": "1"})
        r.status_table("执行结果", [
            ("repo-ok", "ok", "工作区干净"),
            ("repo-skip", "skip", "当前分支与远端同步"),
            ("repo-fail", "fail", "push 被拒绝"),
        ])
        out = r.console.file.getvalue()
        self.assertIn("repo-ok | ok |", out)
        self.assertIn("repo-skip | skip |", out)
        self.assertIn("repo-fail | fail | push 被拒绝", out)
        self.assertNotIn("工作区干净", out)
        self.assertNotIn("当前分支与远端同步", out)

    def test_rule_and_panel(self):
        r = make_reporter({"CLAUDECODE": "1"})
        r.rule("开始")
        self.assertEqual(r.console.file.getvalue(), "")  # rule 直接丢弃
        r.panel("摘要", "第一行\n第二行")
        out = r.console.file.getvalue()
        self.assertIn("摘要", out)  # 成功面板只留标题行
        self.assertNotIn("第一行", out)  # 内容（message/详情）丢弃
        self.assertNotIn("第二行", out)

    def test_output_gated_unless_forced(self):
        r = make_reporter({"CLAUDECODE": "1"})
        r.output("Switched to branch 'dev'")
        self.assertEqual(r.console.file.getvalue(), "")  # 过程性回放默认丢弃
        r.output("fatal: conflict", force=True)
        self.assertIn("fatal: conflict", r.console.file.getvalue())  # 失败详情放行

    def test_cmd_result_success_silent_failure_kept(self):
        r = make_reporter({"CLAUDECODE": "1"})
        r.cmd_result(["git", "merge"], returncode=0)
        self.assertEqual(r.console.file.getvalue(), "")  # 成功静默
        r.cmd_result(["git", "push"], returncode=1, output="rejected", show_output=True)
        out = r.console.file.getvalue()
        self.assertIn("ERROR:", out)
        self.assertIn("rejected", out)


class TestDataTable(unittest.TestCase):
    """data_table 矩阵：kind × AI/人类环境。"""

    def test_kind_table_tsv_in_ai_rich_in_human(self):
        import contextlib

        headers = ["a", "b"]
        rows = [[1, "x\ty"], [None, "z"]]
        # AI：TSV（首行列名，单元格内 \t 转义）
        buf = io.StringIO()
        with patch.dict(os.environ, {"CLAUDECODE": "1"}), contextlib.redirect_stdout(buf):
            make_reporter({"CLAUDECODE": "1"}).data_table(headers, rows)
        self.assertEqual(buf.getvalue().splitlines(),
                         ["a\tb", "1\tx\\ty", "None\tz"])
        # 人类：Rich 框线表格，标题和列头保留
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            make_reporter({}).data_table(headers, rows, title="演示")
        out = buf.getvalue()
        self.assertIn("│", out)
        self.assertIn("演示", out)
        self.assertIn("None", out)

    def test_kind_json_compact_in_ai_indented_in_human(self):
        import contextlib

        payload = {"k": [1, 2]}
        # AI：压缩单行 JSON
        buf = io.StringIO()
        with patch.dict(os.environ, {"CLAUDECODE": "1"}), contextlib.redirect_stdout(buf):
            make_reporter({"CLAUDECODE": "1"}).data_table(None, payload, kind="json")
        self.assertEqual(buf.getvalue(), '{"k":[1,2]}\n')
        # 人类：indent=2
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            make_reporter({}).data_table(None, payload, kind="json")
        self.assertIn('{\n  "k": [', buf.getvalue())


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
        # --table 走 Reporter.data_table：AI 环境由它自动降级 TSV
        self.assertIn("self._r.data_table(columns, values)", src)

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


class TestMinimalRemainingChannels(unittest.TestCase):
    """极简模式下还没被别处覆盖的几条出口。"""

    def _minimal(self):
        buf = io.StringIO()
        with patch.dict(os.environ, {"CLAUDECODE": "1"}, clear=False):
            return Reporter.from_buffer(buf), buf

    def test_status_fail_prints_error_prefix(self):
        r, buf = self._minimal()
        r.status("fail", "编译失败")
        self.assertEqual(buf.getvalue().strip(), "ERROR: 编译失败")

    def test_status_ok_and_skip_stay_silent(self):
        r, buf = self._minimal()
        r.status("ok", "成功了")
        r.status("skip", "跳过了")
        self.assertEqual(buf.getvalue(), "")

    def test_an_unknown_status_keeps_the_message_plain(self):
        r, buf = self._minimal()
        r.status("pending", "排队中")
        self.assertEqual(buf.getvalue().strip(), "排队中")

    def test_warn_keeps_the_original_text_behind_a_prefix(self):
        r, buf = self._minimal()
        r.warn("磁盘快满了")
        self.assertEqual(buf.getvalue().strip(), "WARN: 磁盘快满了")

    def test_status_footer_is_dropped(self):
        r, buf = self._minimal()
        r.status_footer([("成功 3", "green"), ("失败 1", "red")])
        self.assertEqual(buf.getvalue(), "")

    def test_empty_status_footer_is_a_no_op_in_either_mode(self):
        r, buf = self._minimal()
        r.status_footer([])
        self.assertEqual(buf.getvalue(), "")

    def test_summary_degrades_to_key_value_lines(self):
        r, buf = self._minimal()
        r.summary("汇总", [("仓库", "3", "bold"), ("失败", "0", None)])
        self.assertEqual(buf.getvalue().split(), ["仓库:", "3", "失败:", "0"])

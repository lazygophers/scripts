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

    def test_ok_err_kept_plain(self):
        r = make_reporter({"CLAUDECODE": "1"})
        r.ok("合并成功")
        r.err("合并失败")
        out = r.console.file.getvalue()
        self.assertIn("合并成功", out)
        self.assertIn("合并失败", out)
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

#!/usr/bin/env python3
"""Tests for lib.notify (project_done_message / notify / notify_via_n)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import lib.notify as notify_mod


class TestProjectDoneMessage(unittest.TestCase):
    @patch("lib.notify.safe_project_context", create=True)
    def test_message_format(self, _mock):
        # project_done_message 内部 from lib.project import safe_project_context
        with patch("lib.project.safe_project_context", return_value="[org] proj"):
            msg = notify_mod.project_done_message("完成")
        self.assertIn("[org] proj", msg)
        self.assertIn("完成", msg)


class TestNotify(unittest.TestCase):
    @patch("lib.notify.run")
    def test_notify_calls_say(self, mock_run):
        notify_mod.notify("hello")
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], "say")
        self.assertEqual(call_args[1], "hello")

    @patch("lib.notify.run")
    def test_notify_custom_say_cmd(self, mock_run):
        notify_mod.notify("hi", say_cmd="espeak")
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], "espeak")


class TestNotifyViaN(unittest.TestCase):
    @patch("lib.notify.run")
    def test_delegates_to_notify(self, mock_run):
        # notify_via_n 应直接调 notify（忽略 script_dir）
        notify_mod.notify_via_n("msg", script_dir=Path("/tmp"))
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        self.assertIn("say", call_args)

    @patch("lib.notify.run")
    def test_script_dir_ignored(self, mock_run):
        # 不论 script_dir 是否传, 行为一致
        notify_mod.notify_via_n("a")
        notify_mod.notify_via_n("b", script_dir=Path("/x"))
        self.assertEqual(mock_run.call_count, 2)


class TestConsumeDebug(unittest.TestCase):
    def setUp(self):
        # 保留并恢复全局 flag, 避免污染其他用例
        self._prev = notify_mod._DEBUG
        notify_mod._DEBUG = False

    def tearDown(self):
        notify_mod._DEBUG = self._prev

    def test_strips_debug_and_sets_flag(self):
        out = notify_mod.consume_debug(["bin/x", "--debug", "arg"])
        self.assertEqual(out, ["bin/x", "arg"])
        self.assertTrue(notify_mod.is_debug())

    def test_no_debug_keeps_argv(self):
        out = notify_mod.consume_debug(["bin/x", "arg"])
        self.assertEqual(out, ["bin/x", "arg"])
        self.assertFalse(notify_mod.is_debug())

    def test_set_debug_toggles(self):
        notify_mod.set_debug(True)
        self.assertTrue(notify_mod.is_debug())
        notify_mod.set_debug(False)
        self.assertFalse(notify_mod.is_debug())


if __name__ == "__main__":
    unittest.main()

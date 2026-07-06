#!/usr/bin/env python3
"""Tests for lib.notify (project_done_message / notify / notify_via_n)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    @patch("builtins.print")
    def test_notify_calls_say(self, mock_print, mock_run):
        notify_mod.notify("hello")
        mock_print.assert_called_once_with("hello")
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], "say")
        self.assertEqual(call_args[1], "hello")

    @patch("lib.notify.run")
    @patch("builtins.print")
    def test_notify_custom_say_cmd(self, _mock_print, mock_run):
        notify_mod.notify("hi", say_cmd="espeak")
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], "espeak")


class TestNotifyViaN(unittest.TestCase):
    @patch("lib.notify.run")
    @patch("builtins.print")
    def test_delegates_to_notify(self, _mock_print, mock_run):
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


if __name__ == "__main__":
    unittest.main()

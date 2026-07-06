#!/usr/bin/env python3
"""Tests for lib.notify.say_content (n 命令核心)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.notify import say_content


class TestSayContent(unittest.TestCase):
    """say_content: 校验 + 调用 say。"""

    @patch("lib.notify.run_logged")
    def test_success(self, mock_run_logged):
        mock_result = MagicMock(returncode=0)
        mock_run_logged.return_value = mock_result
        assert say_content("Test message") == 0
        call_args = mock_run_logged.call_args[0][0]
        assert call_args == ["say", "Test message"]

    @patch("lib.notify.run_logged")
    def test_failure(self, mock_run_logged):
        mock_run_logged.return_value = MagicMock(returncode=1)
        assert say_content("Test message") == 1

    def test_filter_dangerous_semicolon(self):
        assert say_content("message; rm -rf /") == 1

    def test_filter_dangerous_pipe(self):
        assert say_content("message | cat /etc/passwd") == 1

    def test_filter_dangerous_ampersand(self):
        assert say_content("message & background_task") == 1

    def test_filter_dangerous_dollar(self):
        assert say_content("message $HOME") == 1

    def test_filter_dangerous_backtick(self):
        assert say_content("message `whoami`") == 1

    def test_filter_dangerous_single_quote(self):
        assert say_content("message'test") == 1

    @patch("lib.notify.run_logged")
    def test_length_500_ok(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert say_content("a" * 500) == 0

    def test_length_501_rejected(self):
        assert say_content("a" * 501) == 1

    @patch("lib.notify.run_logged")
    def test_empty_message(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert say_content("") == 0

    @patch("lib.notify.run_logged")
    def test_normal_with_spaces(self, mock_run_logged):
        mock_run_logged.return_value = MagicMock(returncode=0)
        assert say_content("Hello World Test") == 0

    @patch("lib.notify.run_logged")
    def test_with_numbers(self, mock_run_logged):
        mock_run_logged.return_value = MagicMock(returncode=0)
        assert say_content("Build 123 completed") == 0

    @patch("lib.notify.run_logged")
    def test_with_chinese(self, mock_run_logged):
        mock_run_logged.return_value = MagicMock(returncode=0)
        assert say_content("编译成功") == 0

    @patch("lib.notify.run_logged")
    def test_say_call_kwargs(self, mock_run_logged):
        mock_run_logged.return_value = MagicMock(returncode=0)
        say_content("Test notification")
        call_kwargs = mock_run_logged.call_args[1]
        assert call_kwargs["check"] is False
        assert call_kwargs["capture_output"] is True

    def test_multiple_dangerous(self):
        assert say_content("msg; ls | cat & echo $HOME `whoami` 'test'") == 1

    @patch("lib.notify.run_logged")
    def test_boundary_500(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert say_content("x" * 500) == 0

    def test_boundary_501(self):
        assert say_content("x" * 501) == 1

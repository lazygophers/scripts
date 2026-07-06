#!/usr/bin/env python3
"""Tests for lib.build.run_checkwork (checkwork 命令核心)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.build import BuildError, run_checkwork


class TestRunCheckwork(unittest.TestCase):
    """run_checkwork: 编译检查 + 通知。"""

    @patch("lib.build.check_build")
    def test_success_returns_zero(self, mock_check_build):
        mock_check_build.return_value = None
        with patch("lib.notify.notify_via_n") as mock_notify:
            self.assertEqual(run_checkwork(), 0)
            mock_notify.assert_called_once()
            self.assertIn("编译检查完成", mock_notify.call_args[0][0])

    @patch("lib.build.check_build")
    def test_build_error_returns_two(self, mock_check_build):
        mock_check_build.side_effect = BuildError("编译失败: syntax error")
        with patch("lib.notify.notify_via_n") as mock_notify:
            self.assertEqual(run_checkwork(), 2)
            mock_notify.assert_not_called()

    @patch("lib.build.check_build")
    def test_check_build_called_with_project_dir(self, mock_check_build):
        mock_check_build.return_value = None
        with patch("lib.notify.notify_via_n"):
            run_checkwork()
        mock_check_build.assert_called_once()
        call_kwargs = mock_check_build.call_args[1]
        self.assertIn("project_dir", call_kwargs)
        self.assertIsInstance(call_kwargs["project_dir"], Path)
        self.assertIn("log", call_kwargs)

    @patch("lib.build.check_build")
    def test_error_message_includes_exception(self, mock_check_build):
        mock_check_build.side_effect = BuildError("Go build failed: undefined")
        with patch("lib.notify.notify_via_n"):
            self.assertEqual(run_checkwork(), 2)


if __name__ == "__main__":
    unittest.main()

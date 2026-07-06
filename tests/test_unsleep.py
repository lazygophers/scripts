#!/usr/bin/env python3
"""测试 lib.system.prevent_sleep 功能"""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

import lib.system as system


class TestPreventSleepCommandMode(unittest.TestCase):
    """测试命令跟随模式"""

    def setUp(self):
        self.mock_reporter = MagicMock()

    @patch("lib.system.subprocess.Popen")
    def test_command_success(self, mock_popen):
        """命令执行成功"""
        mock_cmd_proc = MagicMock()
        mock_cmd_proc.pid = 12345
        mock_cmd_proc.wait.return_value = 0

        mock_caffeinate_proc = MagicMock()
        mock_caffeinate_proc.poll.return_value = None
        mock_caffeinate_proc.wait.return_value = None

        mock_popen.side_effect = [mock_cmd_proc, mock_caffeinate_proc]

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(command=["echo", "test"])

        self.assertEqual(result, 0)
        mock_cmd_proc.terminate.assert_not_called()
        args, kwargs = mock_popen.call_args_list[1]
        self.assertEqual(args[0], ["caffeinate", "-w", "12345"])
        self.assertNotIn("stdout", kwargs)
        self.assertNotIn("stderr", kwargs)

    @patch("lib.system.subprocess.Popen")
    def test_command_failure(self, mock_popen):
        """命令执行失败"""
        mock_cmd_proc = MagicMock()
        mock_cmd_proc.pid = 12345
        mock_cmd_proc.wait.return_value = 1

        mock_caffeinate_proc = MagicMock()
        mock_caffeinate_proc.poll.return_value = None
        mock_caffeinate_proc.wait.return_value = None

        mock_popen.side_effect = [mock_cmd_proc, mock_caffeinate_proc]

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(command=["false"])

        self.assertEqual(result, 1)

    @patch("lib.system.subprocess.Popen")
    def test_command_keyboard_interrupt(self, mock_popen):
        """键盘中断"""
        mock_cmd_proc = MagicMock()
        mock_cmd_proc.pid = 12345
        mock_cmd_proc.wait.side_effect = [KeyboardInterrupt(), None]

        mock_caffeinate_proc = MagicMock()
        mock_caffeinate_proc.poll.return_value = None
        mock_caffeinate_proc.wait.return_value = None

        mock_popen.side_effect = [mock_cmd_proc, mock_caffeinate_proc]

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(command=["sleep", "10"])

        self.assertEqual(result, 130)
        mock_cmd_proc.terminate.assert_called_once()
        mock_caffeinate_proc.terminate.assert_called_once()

    @patch("lib.system.subprocess.Popen")
    def test_caffeinate_process_died_immediately(self, mock_popen):
        """测试 caffeinate 进程启动后立即退出"""
        mock_cmd_proc = MagicMock()
        mock_cmd_proc.pid = 12345
        mock_cmd_proc.wait.return_value = 0

        mock_caffeinate_proc = MagicMock()
        mock_caffeinate_proc.poll.return_value = 1
        mock_caffeinate_proc.returncode = 1

        mock_popen.side_effect = [mock_cmd_proc, mock_caffeinate_proc]

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(command=["echo", "test"])

        self.assertEqual(result, 1)
        mock_cmd_proc.terminate.assert_called_once()

    @patch("lib.system.subprocess.Popen")
    def test_command_file_not_found(self, mock_popen):
        """测试命令不存在"""
        mock_popen.side_effect = FileNotFoundError("No such file or directory: 'nonexistent_cmd'")

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(command=["nonexistent_cmd"])

        self.assertEqual(result, 127)

    @patch("lib.system.subprocess.Popen")
    def test_command_subprocess_error(self, mock_popen):
        """测试命令启动失败"""
        mock_popen.side_effect = subprocess.SubprocessError("Failed to start process")

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(command=["invalid_cmd"])

        self.assertEqual(result, 1)

    @patch("lib.system.subprocess.Popen")
    def test_negative_exit_code(self, mock_popen):
        """负退出码"""
        mock_cmd_proc = MagicMock()
        mock_cmd_proc.pid = 12345
        mock_cmd_proc.wait.return_value = -15

        mock_caffeinate_proc = MagicMock()
        mock_caffeinate_proc.poll.return_value = None
        mock_caffeinate_proc.wait.return_value = None

        mock_popen.side_effect = [mock_cmd_proc, mock_caffeinate_proc]

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(command=["sleep", "5"])

        self.assertEqual(result, -15)


class TestPreventSleepDurationMode(unittest.TestCase):
    """测试时长模式"""

    def setUp(self):
        self.mock_reporter = MagicMock()

    @patch("lib.system.subprocess.Popen")
    def test_custom_time(self, mock_popen):
        """自定义时长"""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 0

        mock_popen.return_value = mock_proc

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(duration=1800)

        self.assertEqual(result, 0)
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], ["caffeinate", "-t", "1800"])

    @patch("lib.system.subprocess.Popen")
    def test_caffeinate_failure(self, mock_popen):
        """caffeinate 异常终止"""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 1

        mock_popen.return_value = mock_proc

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(duration=900)

        self.assertEqual(result, 1)

    @patch("lib.system.subprocess.Popen")
    def test_time_mode_keyboard_interrupt(self, mock_popen):
        """时长模式键盘中断"""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = [KeyboardInterrupt(), None]

        mock_popen.return_value = mock_proc

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(duration=900)

        self.assertEqual(result, 0)

    @patch("lib.system.subprocess.Popen")
    def test_zero_time(self, mock_popen):
        """零时长"""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 0

        mock_popen.return_value = mock_proc

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(duration=0)

        self.assertEqual(result, 0)
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], ["caffeinate", "-t", "0"])

    @patch("lib.system.subprocess.Popen")
    def test_caffeinate_dies_immediately_time_mode(self, mock_popen):
        """测试时长模式下 caffeinate 进程启动后立即退出"""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        mock_proc.returncode = 1

        mock_popen.return_value = mock_proc

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(duration=900)

        self.assertEqual(result, 1)

    @patch("lib.system.subprocess.Popen")
    def test_caffeinate_file_not_found_time_mode(self, mock_popen):
        """测试时长模式下 caffeinate 命令不存在"""
        mock_popen.side_effect = FileNotFoundError("No such file or directory: 'caffeinate'")

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(duration=900)

        self.assertEqual(result, 1)


class TestPreventSleepUnlimitedMode(unittest.TestCase):
    """测试无限制模式"""

    def setUp(self):
        self.mock_reporter = MagicMock()

    @patch("lib.system.subprocess.Popen")
    def test_unlimited_mode(self, mock_popen):
        """无限制模式"""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 0

        mock_popen.return_value = mock_proc

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep()

        self.assertEqual(result, 0)
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], ["caffeinate"])

    @patch("lib.system.subprocess.Popen")
    def test_unlimited_mode_keyboard_interrupt(self, mock_popen):
        """无限制模式键盘中断"""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = [KeyboardInterrupt(), None]

        mock_popen.return_value = mock_proc

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep()

        self.assertEqual(result, 0)

    @patch("lib.system.subprocess.Popen")
    def test_caffeinate_subprocess_error_unlimited_mode(self, mock_popen):
        """测试无限制模式下 caffeinate 启动失败"""
        mock_popen.side_effect = subprocess.SubprocessError("Failed to start caffeinate")

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep()

        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()

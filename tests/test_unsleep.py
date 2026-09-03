"""测试 lib.system.prevent_sleep 功能"""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

import lib.system as system


@patch("lib.system.sys.platform", "darwin")
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


@patch("lib.system.sys.platform", "darwin")
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


@patch("lib.system.sys.platform", "darwin")
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


@patch("lib.system.sys.platform", "linux")
class TestPreventSleepLinuxMode(unittest.TestCase):
    def setUp(self):
        self.mock_reporter = MagicMock()

    @patch("lib.system.shutil.which", return_value="/usr/bin/systemd-inhibit")
    @patch("lib.system.subprocess.Popen")
    def test_linux_duration_uses_systemd_inhibit(self, mock_popen, mock_which):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(duration=60)

        self.assertEqual(result, 0)
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], ["systemd-inhibit", "--what=sleep", "--why=unsleep", "--", "sleep", "60"])

    @patch("lib.system.shutil.which", return_value=None)
    def test_linux_without_systemd_inhibit_is_unsupported(self, _mock_which):
        with patch("lib.system.reporter", return_value=self.mock_reporter):
            result = system.prevent_sleep(duration=60)

        self.assertEqual(result, 1)
        self.mock_reporter.err.assert_called()


@patch("lib.system.sys.platform", "darwin")
class TestCaffeinateFailuresInCommandMode(unittest.TestCase):
    """命令跟随模式下 caffeinate 本身启动失败：命令进程必须被回收。"""

    def setUp(self):
        self.mock_reporter = MagicMock()

    def _run(self, caffeinate_error):
        cmd_proc = MagicMock()
        cmd_proc.pid = 999
        cmd_proc.wait.return_value = 0
        with patch("lib.system.subprocess.Popen", return_value=cmd_proc), \
             patch("lib.system._start_caffeinate", side_effect=caffeinate_error), \
             patch("lib.system.reporter", return_value=self.mock_reporter):
            rc = system.prevent_sleep(command=["sleep", "1"])
        return rc, cmd_proc

    def test_caffeinate_missing_terminates_command(self):
        rc, cmd_proc = self._run(FileNotFoundError("caffeinate"))
        self.assertEqual(rc, 1)
        cmd_proc.terminate.assert_called_once()
        cmd_proc.wait.assert_called_once()

    def test_caffeinate_subprocess_error_terminates_command(self):
        rc, cmd_proc = self._run(subprocess.SubprocessError("boom"))
        self.assertEqual(rc, 1)
        cmd_proc.terminate.assert_called_once()


@patch("lib.system.sys.platform", "darwin")
class TestRemainingSystemBranches(unittest.TestCase):
    def setUp(self):
        self.mock_reporter = MagicMock()

    def test_duration_mode_subprocess_error(self):
        with patch("lib.system._start_caffeinate",
                   side_effect=subprocess.SubprocessError("boom")), \
             patch("lib.system.reporter", return_value=self.mock_reporter):
            self.assertEqual(system.prevent_sleep(duration=60), 1)

    def test_unlimited_mode_file_not_found(self):
        with patch("lib.system._start_caffeinate", side_effect=FileNotFoundError), \
             patch("lib.system.reporter", return_value=self.mock_reporter):
            self.assertEqual(system.prevent_sleep(), 1)

    def test_unlimited_mode_caffeinate_dies_immediately(self):
        proc = MagicMock()
        proc.poll.return_value = 1
        proc.returncode = 1
        with patch("lib.system._start_caffeinate", return_value=proc), \
             patch("lib.system.reporter", return_value=self.mock_reporter):
            self.assertEqual(system.prevent_sleep(), 1)
        proc.wait.assert_not_called()

    def test_start_caffeinate_uses_popen(self):
        with patch("lib.system.subprocess.Popen", return_value="proc") as mock_popen:
            self.assertEqual(system._start_caffeinate(["caffeinate"]), "proc")
        mock_popen.assert_called_once_with(["caffeinate"])


if __name__ == "__main__":
    unittest.main()

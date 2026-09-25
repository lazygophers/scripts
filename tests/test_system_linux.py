#!/usr/bin/env python3
"""防休眠的 Linux 分支（systemd-inhibit）。

开发机是 macOS，这一整段（lib/system.py 的 _run_inhibited_command）在本机
永远走不到，coverage 一直是空白。这里把 sys.platform 和 Popen 都换掉，
在 macOS 上也能把这条路径跑完。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from lib import system


class LinuxCase(unittest.TestCase):
    """统一：假装在 Linux 上，Reporter 换成 mock。"""

    def setUp(self) -> None:
        self.r = MagicMock()
        patcher = patch.object(sys, "platform", "linux")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, popen) -> int:
        with patch.object(subprocess, "Popen", popen):
            return system._run_inhibited_command(["make", "build"], self.r)


class TestInhibitedCommand(LinuxCase):
    def test_the_command_is_wrapped_in_systemd_inhibit(self):
        popen = MagicMock(return_value=MagicMock(wait=MagicMock(return_value=0)))
        self._run(popen)
        argv, = popen.call_args.args
        self.assertEqual(argv[-2:], ["make", "build"])
        self.assertEqual(argv[: len(system.INHIBIT_ARGS)], list(system.INHIBIT_ARGS))

    def test_a_successful_command_exits_zero(self):
        popen = MagicMock(return_value=MagicMock(wait=MagicMock(return_value=0)))
        self.assertEqual(self._run(popen), 0)
        self.r.ok.assert_called_once()

    def test_a_failing_command_passes_its_exit_code_through(self):
        popen = MagicMock(return_value=MagicMock(wait=MagicMock(return_value=3)))
        self.assertEqual(self._run(popen), 3)
        self.r.warn.assert_called_once()

    def test_a_missing_command_is_127(self):
        self.assertEqual(self._run(MagicMock(side_effect=FileNotFoundError("no make"))), 127)
        self.r.err.assert_called_once()

    def test_a_failed_spawn_is_1(self):
        popen = MagicMock(side_effect=subprocess.SubprocessError("boom"))
        self.assertEqual(self._run(popen), 1)
        self.r.err.assert_called_once()

    def test_ctrl_c_terminates_the_child_and_returns_130(self):
        child = MagicMock()
        child.wait.side_effect = [KeyboardInterrupt, 0]
        self.assertEqual(self._run(MagicMock(return_value=child)), 130)
        child.terminate.assert_called_once()

    def test_the_linux_path_is_the_one_chosen_for_a_wrapped_command(self):
        with patch.object(system, "_run_inhibited_command", return_value=0) as inhibited:
            self.assertEqual(system._prevent_sleep_for_command(["make"], self.r), 0)
        inhibited.assert_called_once()


class TestPlatformSupport(unittest.TestCase):
    def test_linux_needs_systemd_inhibit_on_path(self):
        with patch.object(sys, "platform", "linux"), \
                patch("shutil.which", return_value="/usr/bin/systemd-inhibit"):
            self.assertTrue(system._platform_supported())
        with patch.object(sys, "platform", "linux"), patch("shutil.which", return_value=None):
            self.assertFalse(system._platform_supported())

    def test_macos_is_always_supported(self):
        with patch.object(sys, "platform", "darwin"):
            self.assertTrue(system._platform_supported())

    def test_anything_else_is_not(self):
        with patch.object(sys, "platform", "win32"):
            self.assertFalse(system._platform_supported())


if __name__ == "__main__":
    unittest.main()

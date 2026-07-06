#!/usr/bin/env python3
"""Tests for lib.loop.run_loop."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.loop import run_loop


def _ok_result():
    return MagicMock(returncode=0)


def _fail_result():
    return MagicMock(returncode=1)


class TestRunLoopValidation(unittest.TestCase):
    @patch("lib.loop.run")
    @patch("lib.loop.reporter")
    def test_shell_special_rejected(self, mock_reporter, mock_run):
        mock_reporter.return_value = MagicMock()
        # cmd 含管道但未用 sh -c 包裹
        rc = run_loop(["echo", "a", "|", "cat"])
        self.assertEqual(rc, 2)
        mock_run.assert_not_called()


class TestRunLoopFinite(unittest.TestCase):
    @patch("lib.loop.run")
    @patch("lib.loop.reporter")
    def test_single_success(self, mock_reporter, mock_run):
        mock_reporter.return_value = MagicMock()
        mock_run.return_value = _ok_result()
        self.assertEqual(run_loop(["echo", "test"], count=1), 0)
        self.assertEqual(mock_run.call_count, 1)

    @patch("lib.loop.run")
    @patch("lib.loop.reporter")
    def test_auto_stop_on_first_success(self, mock_reporter, mock_run):
        mock_reporter.return_value = MagicMock()
        mock_run.return_value = _ok_result()
        # auto-stop: count=5 但首次成功即停
        self.assertEqual(run_loop(["echo", "test"], count=5), 0)
        self.assertEqual(mock_run.call_count, 1)

    @patch("lib.loop.run")
    @patch("lib.loop.reporter")
    def test_force_runs_all(self, mock_reporter, mock_run):
        mock_reporter.return_value = MagicMock()
        mock_run.return_value = _ok_result()
        self.assertEqual(run_loop(["echo", "test"], count=5, force=True), 0)
        self.assertEqual(mock_run.call_count, 5)

    @patch("lib.loop.run")
    @patch("lib.loop.reporter")
    def test_force_large_count(self, mock_reporter, mock_run):
        mock_reporter.return_value = MagicMock()
        mock_run.return_value = _ok_result()
        self.assertEqual(run_loop(["echo", "test"], count=100, force=True), 0)
        self.assertEqual(mock_run.call_count, 100)

    @patch("lib.loop.run")
    @patch("lib.loop.reporter")
    def test_command_with_multiple_args(self, mock_reporter, mock_run):
        mock_reporter.return_value = MagicMock()
        mock_run.return_value = _ok_result()
        run_loop(["echo", "hello", "world", "test"], count=2)
        # 验证 run 被以完整 cmd 调用
        mock_run.assert_called_with(["echo", "hello", "world", "test"],
                                    check=False, capture_output=False)

    @patch("lib.loop.run")
    @patch("lib.loop.reporter")
    def test_single_failure(self, mock_reporter, mock_run):
        mock_reporter.return_value = MagicMock()
        mock_run.return_value = _fail_result()
        self.assertEqual(run_loop(["false"], count=1), 1)

    @patch("lib.loop.run")
    @patch("lib.loop.reporter")
    def test_all_failures_force(self, mock_reporter, mock_run):
        mock_reporter.return_value = MagicMock()
        mock_run.return_value = _fail_result()
        self.assertEqual(run_loop(["false"], count=3, force=True), 1)
        self.assertEqual(mock_run.call_count, 3)

    @patch("lib.loop.run")
    @patch("lib.loop.reporter")
    def test_auto_stop_continues_through_failures(self, mock_reporter, mock_run):
        """auto-stop 模式：失败继续，直到成功停止；但有失败记录 → 返 1。"""
        mock_reporter.return_value = MagicMock()
        mock_run.side_effect = [_fail_result(), _fail_result(), _ok_result()]
        self.assertEqual(run_loop(["cmd"], count=5), 1)
        self.assertEqual(mock_run.call_count, 3)

    @patch("lib.loop.run")
    @patch("lib.loop.reporter")
    def test_force_failure_continues(self, mock_reporter, mock_run):
        mock_reporter.return_value = MagicMock()
        mock_run.side_effect = [_fail_result(), _ok_result(), _fail_result()]
        self.assertEqual(run_loop(["cmd"], count=3, force=True), 1)
        self.assertEqual(mock_run.call_count, 3)

    @patch("lib.loop.run")
    @patch("lib.loop.reporter")
    def test_no_force_failures_then_success(self, mock_reporter, mock_run):
        mock_reporter.return_value = MagicMock()
        mock_run.side_effect = [_fail_result(), _fail_result(), _ok_result()]
        # 有失败 → 返 1（即使最终成功停止）
        self.assertEqual(run_loop(["cmd"], count=5), 1)


class TestRunLoopInfinite(unittest.TestCase):
    @patch("lib.loop.run")
    @patch("lib.loop.reporter")
    def test_infinite_auto_stop_on_success(self, mock_reporter, mock_run):
        mock_reporter.return_value = MagicMock()
        mock_run.return_value = _ok_result()
        self.assertEqual(run_loop(["curl", "url"], count=None), 0)
        self.assertEqual(mock_run.call_count, 1)

    @patch("lib.loop.run")
    @patch("lib.loop.reporter")
    def test_explicit_infinite_flag(self, mock_reporter, mock_run):
        mock_reporter.return_value = MagicMock()
        mock_run.return_value = _ok_result()
        self.assertEqual(run_loop(["curl", "url"], infinite=True), 0)
        self.assertEqual(mock_run.call_count, 1)

    @patch("lib.loop.run")
    @patch("lib.loop.reporter")
    def test_infinite_force_runs_multiple(self, mock_reporter, mock_run):
        """infinite + force: 永不停止，会死循环——这里用 side_effect 模拟一次成功后强制中断。"""
        mock_reporter.return_value = MagicMock()
        # side_effect 在第 3 次抛 KeyboardInterrupt 模拟中断
        mock_run.side_effect = [_ok_result(), _ok_result(), KeyboardInterrupt]
        with self.assertRaises(KeyboardInterrupt):
            run_loop(["cmd"], count=None, force=True)
        self.assertGreaterEqual(mock_run.call_count, 3)


if __name__ == "__main__":
    unittest.main()

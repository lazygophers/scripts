#!/usr/bin/env python3
"""Tests for lib.process (kkp 命令核心)."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.process import _lsof_pids, _validate_port, kill_by_port, ps_info


class TestValidatePort(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(_validate_port("1"), 1)
        self.assertEqual(_validate_port("80"), 80)
        self.assertEqual(_validate_port("8080"), 8080)
        self.assertEqual(_validate_port("65535"), 65535)

    def test_invalid_non_numeric(self):
        with self.assertRaisesRegex(ValueError, "端口号必须是数字"):
            _validate_port("abc")

    def test_invalid_low(self):
        with self.assertRaisesRegex(ValueError, "端口号必须在 1-65535 范围内"):
            _validate_port("0")

    def test_invalid_high(self):
        with self.assertRaisesRegex(ValueError, "端口号必须在 1-65535 范围内"):
            _validate_port("65536")

    def test_negative(self):
        with self.assertRaisesRegex(ValueError, "端口号必须是数字"):
            _validate_port("-1")

    def test_with_spaces(self):
        with self.assertRaisesRegex(ValueError, "端口号必须是数字"):
            _validate_port("80 80")


class TestLsofPids(unittest.TestCase):
    @patch("lib.process.run")
    def test_find_processes(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
                "python   1234 user    3u  IPv4  12345      0t0  TCP *:8080 (LISTEN)\n"
                "node     5678 user    4u  IPv4  12346      0t0  TCP *:8080 (LISTEN)\n"
            ),
        )
        pids = _lsof_pids(8080, 9999, set())
        self.assertEqual(pids, [1234, 5678])
        mock_run.assert_called_once_with(
            ["lsof", "-i:8080", "-n", "-P"], check=False, capture_output=True
        )

    @patch("lib.process.run")
    def test_no_processes(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        self.assertEqual(_lsof_pids(8080, os.getpid(), set()), [])

    @patch("lib.process.run")
    def test_filter_self(self, mock_run):
        current_pid = os.getpid()
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                f"COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
                f"python   {current_pid} user    3u  IPv4  12345      0t0  TCP *:8080 (LISTEN)\n"
                f"node     5678 user    4u  IPv4  12346      0t0  TCP *:8080 (LISTEN)\n"
            ),
        )
        self.assertEqual(_lsof_pids(8080, current_pid, set()), [5678])

    @patch("lib.process.run")
    def test_invalid_pid_lines(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
                "python   1234 user    3u  IPv4  12345      0t0  TCP *:8080 (LISTEN)\n"
                "invalid  line here\n"
                "node     5678 user    4u  IPv4  12346      0t0  TCP *:8080 (LISTEN)\n"
            ),
        )
        self.assertEqual(_lsof_pids(8080, os.getpid(), set()), [1234, 5678])

    @patch("lib.process.run")
    def test_markers_exclude_self_helper(self, mock_run):
        """markers 非空时, ps command 含 marker 的 pid 被排除（kkp 自身 helper）。"""
        # lsof 返 2 个 pid; ps 查 1234 返含 "kkp" 的命令行, 5678 不含
        def fake_run(cmd, **kw):
            if cmd[0] == "lsof":
                return MagicMock(returncode=0, stdout=(
                    "COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
                    "python   1234 user    3u  IPv4  12345      0t0  TCP *:8080 (LISTEN)\n"
                    "node     5678 user    4u  IPv4  12346      0t0  TCP *:8080 (LISTEN)\n"
                ))
            # ps -p <pid> -o command=
            return MagicMock(returncode=0, stdout=f"python {cmd[2]}/bin/kkp\n" if cmd[2] == "1234" else "node app.js\n")
        mock_run.side_effect = fake_run
        pids = _lsof_pids(8080, 9999, {"kkp", "kkp.py"})
        self.assertEqual(pids, [5678])


class TestPsInfo(unittest.TestCase):
    @patch("lib.process.run")
    def test_normal(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="1234 user1 python python test.py\n5678 user2 node node app.js\n",
        )
        info = ps_info([1234, 5678])
        self.assertEqual(len(info), 2)
        self.assertEqual(info[1234], ("1234", "user1", "python", "python test.py"))
        self.assertEqual(info[5678], ("5678", "user2", "node", "node app.js"))

    @patch("lib.process.run")
    def test_empty_list(self, mock_run):
        self.assertEqual(ps_info([]), {})
        mock_run.assert_not_called()

    @patch("lib.process.run")
    def test_invalid_lines(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="1234 user1 python python test.py\ninvalid line\n5678 user2 node\n",
        )
        info = ps_info([1234, 5678])
        self.assertEqual(len(info), 1)
        self.assertIn(1234, info)


class TestKillByPort(unittest.TestCase):
    def test_invalid_port(self):
        self.assertEqual(kill_by_port("invalid"), 1)

    @patch("lib.process._lsof_pids")
    def test_no_process_on_port(self, mock_lsof):
        mock_lsof.return_value = []
        self.assertEqual(kill_by_port("8080"), 0)

    @patch("lib.process.os.kill")
    @patch("lib.process.ps_info")
    @patch("lib.process._lsof_pids")
    def test_kill_success(self, mock_lsof, mock_ps_info, mock_kill):
        mock_lsof.return_value = [1234]
        mock_ps_info.return_value = {1234: ("1234", "user", "python", "python server.py")}
        self.assertEqual(kill_by_port("8080"), 0)
        mock_kill.assert_called_once_with(1234, 9)

    @patch("lib.process.os.kill")
    @patch("lib.process.ps_info")
    @patch("lib.process._lsof_pids")
    def test_kill_multiple(self, mock_lsof, mock_ps_info, mock_kill):
        mock_lsof.return_value = [1234, 5678]
        mock_ps_info.return_value = {
            1234: ("1234", "user", "python", "a"),
            5678: ("5678", "user", "node", "b"),
        }
        self.assertEqual(kill_by_port("8080"), 0)
        self.assertEqual(mock_kill.call_count, 2)

    @patch("lib.process.ps_info")
    @patch("lib.process._lsof_pids")
    def test_dry_run_no_kill(self, mock_lsof, mock_ps_info):
        mock_lsof.return_value = [1234]
        mock_ps_info.return_value = {1234: ("1234", "user", "python", "a")}
        with patch("lib.process.os.kill") as mock_kill:
            self.assertEqual(kill_by_port("8080", dry_run=True), 0)
            mock_kill.assert_not_called()

    @patch("lib.process.os.kill")
    @patch("lib.process.ps_info")
    @patch("lib.process._lsof_pids")
    def test_kill_failure_returns_one(self, mock_lsof, mock_ps_info, mock_kill):
        mock_lsof.return_value = [1234]
        mock_ps_info.return_value = {1234: ("1234", "user", "python", "a")}
        mock_kill.side_effect = ProcessLookupError("no such process")
        self.assertEqual(kill_by_port("8080"), 1)


if __name__ == "__main__":
    unittest.main()

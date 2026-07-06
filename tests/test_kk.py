#!/usr/bin/env python3
"""Tests for lib.process.kill_by_name"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.process import _pgrep, kill_by_name
from lib.process import ps_info as _ps_info_fn


class TestPgrep:
    @patch("lib.process.run")
    def test_pgrep_find_processes(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1234\n5678\n"
        mock_run.return_value = mock_result
        pids = _pgrep("python")
        assert pids == [1234, 5678]
        mock_run.assert_called_once_with(
            ["pgrep", "-f", "python"], check=False, capture_output=True
        )

    @patch("lib.process.run")
    def test_pgrep_no_processes_found(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_run.return_value = mock_result
        assert _pgrep("nonexistent") == []

    @patch("lib.process.run")
    def test_pgrep_empty_output(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_run.return_value = mock_result
        assert _pgrep("test") == []

    @patch("lib.process.run")
    def test_pgrep_invalid_pid_lines(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1234\ninvalid\n5678\n"
        mock_run.return_value = mock_result
        assert _pgrep("test") == [1234, 5678]


class TestPsInfo:
    @patch("lib.process.run")
    def test_ps_info(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1234 luoxin python /usr/bin/python-test 999\n"
        mock_run.return_value = mock_result
        info = _ps_info_fn([1234], include_ppid=True)
        assert 1234 in info
        assert info[1234][1] == "luoxin"
        assert info[1234][3] == "/usr/bin/python-test"

    @patch("lib.process.run")
    def test_ps_info_no_args(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1234 luoxin bash - 999\n"
        mock_run.return_value = mock_result
        info = _ps_info_fn([1234], include_ppid=True)
        assert 1234 in info
        assert info[1234][1] == "luoxin"

    @patch("lib.process.run")
    def test_ps_info_process_not_found(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_run.return_value = mock_result
        assert 9999 not in _ps_info_fn([9999])


class TestKillByName:
    """Test lib.process.kill_by_name functionality."""

    def test_invalid_process_name_characters(self):
        """非法字符直接返回 1。"""
        assert kill_by_name(["process;name"]) == 1

    @patch("lib.process.os.kill")
    @patch("lib.process.ps_info")
    @patch("lib.process._pgrep")
    def test_kill_processes_success(self, mock_pgrep, mock_ps_info, mock_kill):
        mock_pgrep.return_value = [1234, 5678]
        mock_ps_info.return_value = {
            1234: ["1234", "user1", "100", "python test1.py"],
            5678: ["5678", "user2", "100", "python test2.py"],
        }
        assert kill_by_name(["python"]) == 0
        assert mock_kill.call_count == 2

    @patch("lib.process.os.kill")
    @patch("lib.process.ps_info")
    @patch("lib.process._pgrep")
    def test_filter_self_process(self, mock_pgrep, mock_ps_info, mock_kill):
        current_pid = os.getpid()
        mock_pgrep.return_value = [current_pid, 5678]
        mock_ps_info.return_value = {
            5678: ["5678", "user", "100", "python other.py"],
        }
        kill_by_name(["python"])
        assert mock_kill.call_count == 1

    @patch("lib.process.os.kill")
    @patch("lib.process.ps_info")
    @patch("lib.process._pgrep")
    def test_filter_script_markers(self, mock_pgrep, mock_ps_info, mock_kill):
        """script_markers 由调用方传入, 命中则跳过。"""
        mock_pgrep.return_value = [1234, 5678]
        mock_ps_info.return_value = {
            1234: ["1234", "user1", "100", "python kk.py test"],
            5678: ["5678", "user2", "100", "python other.py"],
        }
        kill_by_name(["python"], script_markers={"kk.py"})
        assert mock_kill.call_count == 1

    @patch("lib.process.ps_info")
    @patch("lib.process._pgrep")
    def test_no_processes_found(self, mock_pgrep, mock_ps_info):
        mock_pgrep.return_value = []
        assert kill_by_name(["nonexistent"]) == 0

    @patch("lib.process.os.kill")
    @patch("lib.process.ps_info")
    @patch("lib.process._pgrep")
    def test_kill_partial_failure(self, mock_pgrep, mock_ps_info, mock_kill):
        mock_pgrep.return_value = [1234, 5678]
        mock_ps_info.return_value = {
            1234: ["1234", "user1", "100", "python test1.py"],
            5678: ["5678", "user2", "100", "python test2.py"],
        }
        mock_kill.side_effect = [None, Exception("Permission denied")]
        assert kill_by_name(["python"]) == 1

    @patch("lib.process.ps_info")
    @patch("lib.process._pgrep")
    def test_skip_processes_without_args(self, mock_pgrep, mock_ps_info):
        mock_pgrep.return_value = [1234, 5678]
        mock_ps_info.return_value = {
            1234: ["1234", "user1", "100", ""],
            5678: ["5678", "user2", "100", "python test.py"],
        }
        with patch("lib.process.os.kill") as mock_kill:
            kill_by_name(["python"])
        assert mock_kill.call_count == 1

    @patch("lib.process.os.kill")
    @patch("lib.process.ps_info")
    @patch("lib.process._pgrep")
    def test_multiple_pids_single_pattern(self, mock_pgrep, mock_ps_info, mock_kill):
        mock_pgrep.return_value = [1234, 5678]
        mock_ps_info.return_value = {
            1234: ["1234", "user1", "100", "python test.py"],
            5678: ["5678", "user2", "100", "python app.js"],
        }
        assert kill_by_name(["python"]) == 0
        assert mock_kill.call_count == 2

    def test_valid_process_names(self):
        with patch("lib.process._pgrep", return_value=[]), patch("lib.process.ps_info", return_value={}):
            assert kill_by_name(["python", "node-app", "test_script.py"]) == 0

    def test_invalid_special_characters(self):
        for name in ["proc;name", "proc|name", "proc&name", "proc$name"]:
            assert kill_by_name([name]) == 1

    @patch("lib.process.ps_info")
    @patch("lib.process._pgrep")
    def test_dry_run_does_not_kill(self, mock_pgrep, mock_ps_info):
        mock_pgrep.return_value = [1234]
        mock_ps_info.return_value = {
            1234: ["1234", "user", "100", "python test.py"],
        }
        with patch("lib.process.os.kill") as mock_kill:
            assert kill_by_name(["python"], dry_run=True) == 0
        assert mock_kill.call_count == 0

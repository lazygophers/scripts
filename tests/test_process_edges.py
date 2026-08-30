#!/usr/bin/env python3
"""lib.process 边界分支：ps 输出畸形、表格降级、lsof 噪声行。"""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import lib.process as P


def _proc(stdout="", returncode=0):
    return MagicMock(returncode=returncode, stdout=stdout, stderr="")


class _PlainReporter:
    """console=None 的 Reporter 替身，触发纯文本降级。"""

    console = None

    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def step(self, m):
        self.lines.append(("step", m))

    def info(self, m):
        self.lines.append(("info", m))


class TestKillByNameEdges(unittest.TestCase):
    def test_no_patterns_finds_nothing(self):
        r = MagicMock()
        with patch.object(P, "reporter", return_value=r):
            self.assertEqual(P.kill_by_name([]), 0)
        r.ok.assert_called_once()

    def test_pid_without_ps_row_is_dropped(self):
        r = MagicMock()
        with patch.object(P, "reporter", return_value=r), \
             patch.object(P, "_pgrep", return_value=[4242]), \
             patch.object(P, "ps_info", return_value={}):
            self.assertEqual(P.kill_by_name(["node"]), 0)
        r.ok.assert_called_once()


class TestPsInfo(unittest.TestCase):
    def test_short_lines_skipped(self):
        with patch.object(P, "run", return_value=_proc("123 alice\n")):
            self.assertEqual(P.ps_info([123]), {})

    def test_non_numeric_pid_skipped(self):
        out = "abc alice node /usr/bin/node app.js 1\n"
        with patch.object(P, "run", return_value=_proc(out)):
            self.assertEqual(P.ps_info([123], include_ppid=True), {})

    def test_empty_pid_list_short_circuits(self):
        with patch.object(P, "run") as mock_run:
            self.assertEqual(P.ps_info([]), {})
        mock_run.assert_not_called()


class TestMakeProcessTable(unittest.TestCase):
    def _rich_reporter(self):
        from lib.ui import Reporter
        buf = io.StringIO()
        return Reporter(file=buf), buf

    def test_rich_table_with_ppid(self):
        r, buf = self._rich_reporter()
        info = {1: ("1", "alice", "node", "node app.js", "99")}
        P.make_process_table([1], info, "进程列表", r, include_ppid=True)
        out = buf.getvalue()
        self.assertIn("PPID", out)
        self.assertIn("99", out)
        self.assertIn("alice", out)

    def test_plain_fallback_with_ppid(self):
        r = _PlainReporter()
        info = {1: ("1", "alice", "node", "node app.js", "99")}
        P.make_process_table([1], info, "进程列表", r, include_ppid=True)
        text = " ".join(m for _, m in r.lines)
        self.assertIn("PID=1", text)
        self.assertIn("PPID=99", text)

    def test_plain_fallback_without_ppid(self):
        r = _PlainReporter()
        info = {1: ("1", "alice", "node", "node app.js")}
        P.make_process_table([1], info, "进程列表", r)
        text = " ".join(m for _, m in r.lines)
        self.assertIn("ARGS=node app.js", text)
        self.assertNotIn("PPID", text)

    def test_plain_fallback_skips_unknown_pid(self):
        r = _PlainReporter()
        P.make_process_table([7], {}, "进程列表", r)
        self.assertEqual([k for k, _ in r.lines], ["step"])


class TestLsofParsing(unittest.TestCase):
    def test_short_line_skipped(self):
        out = "COMMAND PID USER\nnode\n"
        with patch.object(P, "run", return_value=_proc(out)):
            self.assertEqual(P._lsof_pids(8080, 1, set()), [])

    def test_non_numeric_pid_skipped(self):
        out = "COMMAND PID USER\nnode xyz alice\n"
        with patch.object(P, "run", return_value=_proc(out)):
            self.assertEqual(P._lsof_pids(8080, 1, set()), [])

    def test_self_pid_excluded(self):
        out = "COMMAND PID USER\nnode 55 alice\n"
        with patch.object(P, "run", return_value=_proc(out)):
            self.assertEqual(P._lsof_pids(8080, 55, set()), [])


class TestCmdlineMatchesMarkers(unittest.TestCase):
    def test_empty_cmdline_is_no_match(self):
        with patch.object(P, "run", return_value=_proc("  \n")):
            self.assertFalse(P._cmdline_matches_markers(1, {"kkp"}))

    def test_marker_found(self):
        with patch.object(P, "run", return_value=_proc("/bin/bash bin/kkp 8080\n")):
            self.assertTrue(P._cmdline_matches_markers(1, {"kkp"}))


if __name__ == "__main__":
    unittest.main()

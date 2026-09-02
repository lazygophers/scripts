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


    def test_illegal_pattern_rejected(self):
        r = MagicMock()
        with patch.object(P, "reporter", return_value=r):
            self.assertEqual(P.kill_by_name(["no;rm -rf"]), 1)
        r.err.assert_called_once()

    def test_multi_pattern_takes_intersection(self):
        r = MagicMock()
        pgrep = {"node": [1, 2, 3], "app": [2, 3]}
        info = {
            2: ("2", "alice", "node", "node app.js", "1"),
            3: ("3", "alice", "node", "node app.js", "1"),
        }
        with patch.object(P, "reporter", return_value=r), \
             patch.object(P, "_pgrep", side_effect=lambda t: pgrep[t]), \
             patch.object(P, "ps_info", return_value=info), \
             patch.object(P, "make_process_table"), \
             patch.object(P, "kill_pids", return_value=([2, 3], [])) as m_kill:
            self.assertEqual(P.kill_by_name(["node", "app"]), 0)
        self.assertEqual(m_kill.call_args[0][0], [2, 3])

    def test_script_marker_process_excluded(self):
        r = MagicMock()
        info = {
            5: ("5", "alice", "bash", "/bin/bash bin/kk node", "1"),
            6: ("6", "alice", "node", "", "1"),
        }
        with patch.object(P, "reporter", return_value=r), \
             patch.object(P, "_pgrep", return_value=[5, 6]), \
             patch.object(P, "ps_info", return_value=info), \
             patch.object(P, "kill_pids") as m_kill:
            self.assertEqual(P.kill_by_name(["node"], script_markers={"bin/kk"}), 0)
        m_kill.assert_not_called()
        r.ok.assert_called_once()

    def test_dry_run_does_not_kill(self):
        r = MagicMock()
        info = {7: ("7", "alice", "node", "node app.js", "1")}
        with patch.object(P, "reporter", return_value=r), \
             patch.object(P, "_pgrep", return_value=[7]), \
             patch.object(P, "ps_info", return_value=info), \
             patch.object(P, "make_process_table"), \
             patch.object(P, "kill_pids") as m_kill:
            self.assertEqual(P.kill_by_name(["node"], dry_run=True), 0)
        m_kill.assert_not_called()
        self.assertIn("dry-run", r.step.call_args[0][0])

    def test_kill_failure_returns_1(self):
        r = MagicMock()
        info = {8: ("8", "alice", "node", "node app.js", "1")}
        with patch.object(P, "reporter", return_value=r), \
             patch.object(P, "_pgrep", return_value=[8]), \
             patch.object(P, "ps_info", return_value=info), \
             patch.object(P, "make_process_table"), \
             patch.object(P, "kill_pids", return_value=([], [8])):
            self.assertEqual(P.kill_by_name(["node"]), 1)

    def test_self_pid_never_killed(self):
        r = MagicMock()
        import os
        with patch.object(P, "reporter", return_value=r), \
             patch.object(P, "_pgrep", return_value=[os.getpid()]), \
             patch.object(P, "ps_info", return_value={}) as m_info, \
             patch.object(P, "kill_pids") as m_kill:
            self.assertEqual(P.kill_by_name(["node"]), 0)
        self.assertEqual(m_info.call_args[0][0], [])
        m_kill.assert_not_called()


class TestPgrep(unittest.TestCase):
    def test_returns_pids(self):
        with patch.object(P, "run", return_value=_proc("11\n12\n\n")):
            self.assertEqual(P._pgrep("node"), [11, 12])

    def test_nonzero_returncode_is_empty(self):
        with patch.object(P, "run", return_value=_proc("11\n", returncode=1)):
            self.assertEqual(P._pgrep("node"), [])

    def test_empty_output_is_empty(self):
        with patch.object(P, "run", return_value=_proc("  \n")):
            self.assertEqual(P._pgrep("node"), [])

    def test_non_numeric_line_skipped(self):
        with patch.object(P, "run", return_value=_proc("11\nnope\n")):
            self.assertEqual(P._pgrep("node"), [11])


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

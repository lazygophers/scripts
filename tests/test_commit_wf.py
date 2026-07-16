#!/usr/bin/env python3
"""Tests for lib.commit_wf."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.commit_wf import _build_prompt, run_commit


class TestBuildPrompt(unittest.TestCase):
    def test_includes_data_marker(self):
        p = _build_prompt(["M  file.py"])
        self.assertIn("<<<DATA>>>", p)
        self.assertIn("file.py", p)

    def test_empty_status(self):
        p = _build_prompt([])
        self.assertIn("（无）", p)

    def test_includes_specs(self):
        p = _build_prompt(["x"])
        self.assertIn("type", p)
        self.assertIn("deps", p)
        self.assertIn("ci", p)


class TestRunCommit(unittest.TestCase):
    @patch("lib.commit_wf._has_changes")
    def test_no_changes_returns_zero(self, mock_has):
        mock_has.return_value = (False, [])
        self.assertEqual(run_commit(), 0)

    @patch("lib.commit_wf.generate_via_claude")
    @patch("lib.commit_wf.run")
    @patch("lib.commit_wf._has_changes")
    def test_with_msg_skips_generate(self, mock_has, mock_run, mock_gen):
        mock_has.return_value = (True, ["M  file.py"])
        mock_run.return_value = MagicMock(stdout="file.py\n", stderr="", returncode=0)
        rc = run_commit("msg")
        self.assertEqual(rc, 0)
        mock_gen.assert_not_called()

    @patch("lib.commit_wf.generate_via_claude")
    @patch("lib.commit_wf.run")
    @patch("lib.commit_wf._has_changes")
    def test_no_msg_calls_generate(self, mock_has, mock_run, mock_gen):
        mock_has.return_value = (True, ["M  file.py"])
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        mock_gen.return_value = "fix: 修 bug"
        rc = run_commit()
        self.assertEqual(rc, 0)
        mock_gen.assert_called_once()

    @patch("lib.commit_wf.generate_via_claude")
    @patch("lib.commit_wf.run")
    @patch("lib.commit_wf._has_changes")
    def test_generate_empty_aborts(self, mock_has, mock_run, mock_gen):
        mock_has.return_value = (True, ["M  file.py"])
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        mock_gen.return_value = ""
        rc = run_commit()
        self.assertEqual(rc, 1)

    @patch("lib.commit_wf.run")
    @patch("lib.commit_wf._has_changes")
    def test_empty_staged_calls_bit_add(self, mock_has, mock_run):
        mock_has.return_value = (True, ["?? new.py"])
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        with patch("lib.commit_wf.generate_via_claude") as mock_gen:
            mock_gen.return_value = "feat: 加文件"
            run_commit()
        calls = [c.args[0] for c in mock_run.call_args_list]
        self.assertIn(["bit", "add", "."], calls)

    @patch("lib.commit_wf.run")
    @patch("lib.commit_wf._has_changes")
    def test_dry_run_no_generate(self, mock_has, mock_run):
        mock_has.return_value = (True, ["M  f"])
        mock_run.return_value = MagicMock(stdout="f\n", stderr="", returncode=0)
        with patch("lib.commit_wf.generate_via_claude") as mock_gen:
            rc = run_commit(dry_run=True)
        self.assertEqual(rc, 0)
        mock_gen.assert_not_called()


if __name__ == "__main__":
    unittest.main()

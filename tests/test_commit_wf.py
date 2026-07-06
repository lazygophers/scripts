#!/usr/bin/env python3
"""Tests for lib.commit_wf."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.commit_wf import _build_prompt, run_commit


class TestBuildPrompt(unittest.TestCase):
    def test_includes_message(self):
        p = _build_prompt("fix: 修复 bug")
        self.assertIn("fix: 修复 bug", p)
        self.assertIn("bit commit", p)

    def test_no_message(self):
        p = _build_prompt(None)
        self.assertIn("无", p)

    def test_includes_specs(self):
        p = _build_prompt("x")
        self.assertIn("type", p)
        self.assertIn("deps", p)
        self.assertIn("ci", p)


class TestRunCommit(unittest.TestCase):
    @patch("lib.commit_wf._has_changes")
    def test_no_changes_returns_zero(self, mock_has):
        mock_has.return_value = (False, [])
        self.assertEqual(run_commit(), 0)

    @patch("lib.commit_wf.run_claude")
    @patch("lib.commit_wf.run")
    @patch("lib.commit_wf._has_changes")
    def test_with_staged_calls_claude(self, mock_has, mock_run, mock_claude):
        mock_has.return_value = (True, ["M  file.py"])
        mock_run.return_value = MagicMock(stdout="file.py\n", stderr="")
        mock_claude.return_value = 0
        rc = run_commit("msg")
        self.assertEqual(rc, 0)
        mock_claude.assert_called_once()

    @patch("lib.commit_wf.run_claude")
    @patch("lib.commit_wf.run")
    @patch("lib.commit_wf._has_changes")
    def test_empty_staged_calls_bit_add(self, mock_has, mock_run, mock_claude):
        mock_has.return_value = (True, ["?? new.py"])
        mock_run.return_value = MagicMock(stdout="", stderr="")
        mock_claude.return_value = 0
        run_commit()
        calls = [c.args[0] for c in mock_run.call_args_list]
        self.assertIn(["bit", "add", "."], calls)

    @patch("lib.commit_wf.run")
    @patch("lib.commit_wf._has_changes")
    def test_dry_run_no_claude(self, mock_has, mock_run):
        mock_has.return_value = (True, ["M  f"])
        mock_run.return_value = MagicMock(stdout="f\n", stderr="")
        with patch("lib.commit_wf.run_claude") as mock_claude:
            rc = run_commit(dry_run=True)
        self.assertEqual(rc, 0)
        mock_claude.assert_not_called()

    @patch("lib.commit_wf.run_claude")
    @patch("lib.commit_wf.run")
    @patch("lib.commit_wf._has_changes")
    def test_propagates_settings_file(self, mock_has, mock_run, mock_claude):
        mock_has.return_value = (True, ["M  f"])
        mock_run.return_value = MagicMock(stdout="f\n", stderr="")
        mock_claude.return_value = 0
        run_commit(settings_file="/x.json")
        kwargs = mock_claude.call_args[1]
        self.assertEqual(kwargs["settings_file"], "/x.json")


if __name__ == "__main__":
    unittest.main()

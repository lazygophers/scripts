#!/usr/bin/env python3
"""Tests for lib.issue_wf."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.ai_workflow import ProviderInfo, fmt_opt
from lib.issue_wf import _build_prompt, run_issue

_INFO = ProviderInfo(provider="gh", host="github.com", repo="o/r",
                     remote="origin", remote_url="git@github.com:o/r.git")


class TestFmtOpt(unittest.TestCase):
    def test_with_value(self):
        self.assertEqual(fmt_opt("--label", "bug"), "--label bug")

    def test_none(self):
        self.assertEqual(fmt_opt("--label", None), "")


class TestBuildPrompt(unittest.TestCase):
    def test_gh(self):
        p = _build_prompt(_INFO, title="bug x", labels="bug", assignee="me", milestone=None)
        self.assertIn("gh issue create", p)
        self.assertIn("--label", p)
        self.assertIn("--assignee", p)
        self.assertIn("bug x", p)

    def test_glab(self):
        info = ProviderInfo(provider="glab", host="gitlab.com", repo="g/p",
                            remote="origin", remote_url="")
        p = _build_prompt(info, title=None, labels=None, assignee=None, milestone="m1")
        self.assertIn("glab issue create", p)
        self.assertIn("--description", p)
        self.assertIn("--milestone", p)

    def test_no_title_shows_infers(self):
        p = _build_prompt(_INFO, title=None, labels=None, assignee=None, milestone=None)
        self.assertIn("推断", p)


class TestRunIssue(unittest.TestCase):
    @patch("lib.issue_wf.run_claude")
    @patch("lib.issue_wf.detect_self_assignee", return_value="")
    @patch("lib.issue_wf.detect_provider")
    def test_success(self, mock_provider, _me, mock_claude):
        mock_provider.return_value = _INFO
        mock_claude.return_value = 0
        self.assertEqual(run_issue("title"), 0)
        mock_claude.assert_called_once()

    @patch("lib.issue_wf.detect_provider", return_value=None)
    def test_no_provider(self, _):
        self.assertEqual(run_issue(), 1)

    @patch("lib.issue_wf.detect_self_assignee", return_value="myuser")
    @patch("lib.issue_wf.detect_provider")
    def test_default_assignee(self, mock_provider, _me):
        mock_provider.return_value = _INFO
        with patch("lib.issue_wf.run_claude", return_value=0) as mock_claude:
            run_issue("x")
        prompt = mock_claude.call_args[0][0]
        self.assertIn("myuser", prompt)

    @patch("lib.issue_wf.detect_self_assignee", return_value="")
    @patch("lib.issue_wf.detect_provider")
    def test_dry_run(self, mock_provider, _me):
        mock_provider.return_value = _INFO
        with patch("lib.issue_wf.run_claude") as mock_claude:
            rc = run_issue("t", dry_run=True)
        self.assertEqual(rc, 0)
        mock_claude.assert_not_called()


if __name__ == "__main__":
    unittest.main()

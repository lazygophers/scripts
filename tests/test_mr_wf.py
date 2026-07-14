#!/usr/bin/env python3
"""Tests for lib.mr_wf."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.ai_workflow import ProviderInfo, fmt_opt
from lib.mr_wf import _build_prompt, run_mr

_INFO = ProviderInfo(provider="gh", host="github.com", repo="o/r",
                     remote="origin", remote_url="git@github.com:o/r.git")


class TestFmtOpt(unittest.TestCase):
    def test_with_value(self):
        self.assertEqual(fmt_opt("--reviewer", "alice"), "--reviewer alice")

    def test_with_special_chars_quoted(self):
        # 含特殊字符的 value 用 shlex.quote 包裹
        out = fmt_opt("--reviewer", "a b")
        self.assertIn("'a b'", out)

    def test_none(self):
        self.assertEqual(fmt_opt("--reviewer", None), "")

    def test_empty(self):
        self.assertEqual(fmt_opt("--reviewer", ""), "")


class TestBuildPrompt(unittest.TestCase):
    def test_gh_command(self):
        p = _build_prompt(_INFO, branch="feat", base="main", draft=True,
                          reviews="bob", labels=None, assignee="me")
        self.assertIn("gh pr create", p)
        self.assertIn("--base main", p)
        self.assertIn("--draft", p)
        self.assertIn("--reviewer", p)
        self.assertIn("bob", p)

    def test_glab_command(self):
        info = ProviderInfo(provider="glab", host="gitlab.com", repo="g/p",
                            remote="origin", remote_url="")
        p = _build_prompt(info, branch="x", base="main", draft=False,
                          reviews=None, labels=None, assignee=None)
        self.assertIn("glab mr create", p)
        self.assertIn("--target-branch main", p)
        self.assertNotIn("--draft", p)

    def test_includes_specs(self):
        p = _build_prompt(_INFO, branch="x", base="main", draft=True,
                          reviews=None, labels=None, assignee=None)
        self.assertIn("title", p)
        self.assertIn("Summary", p)


class TestRunPrc(unittest.TestCase):
    @patch("lib.mr_wf.run_claude")
    @patch("lib.mr_wf.detect_self_assignee", return_value="")
    @patch("lib.mr_wf.remote_default_branch", return_value="main")
    @patch("lib.mr_wf.current_branch", return_value="feat")
    @patch("lib.mr_wf.detect_provider")
    def test_success(self, mock_provider, _b, _a, _me, mock_claude):
        mock_provider.return_value = _INFO
        mock_claude.return_value = 0
        rc = run_mr()
        self.assertEqual(rc, 0)
        mock_claude.assert_called_once()

    @patch("lib.mr_wf.detect_provider", return_value=None)
    def test_no_provider(self, _):
        self.assertEqual(run_mr(), 1)

    @patch("lib.mr_wf.run_claude")
    @patch("lib.mr_wf.detect_self_assignee", return_value="myuser")
    @patch("lib.mr_wf.remote_default_branch", return_value="main")
    @patch("lib.mr_wf.current_branch", return_value="feat")
    @patch("lib.mr_wf.detect_provider")
    def test_default_assignee_self(self, mock_provider, _b, _a, _me, mock_claude):
        mock_provider.return_value = _INFO
        mock_claude.return_value = 0
        run_mr()
        kwargs = mock_claude.call_args[0][0]
        self.assertIn("myuser", kwargs)

    @patch("lib.mr_wf.detect_self_assignee", return_value="")
    @patch("lib.mr_wf.remote_default_branch", return_value="main")
    @patch("lib.mr_wf.current_branch", return_value="feat")
    @patch("lib.mr_wf.detect_provider")
    def test_dry_run_no_claude(self, mock_provider, _b, _a, _me):
        mock_provider.return_value = _INFO
        with patch("lib.mr_wf.run_claude") as mock_claude:
            rc = run_mr(dry_run=True)
        self.assertEqual(rc, 0)
        mock_claude.assert_not_called()

    @patch("lib.mr_wf.detect_self_assignee", return_value="")
    @patch("lib.mr_wf.remote_default_branch", return_value="main")
    @patch("lib.mr_wf.current_branch", return_value="feat")
    @patch("lib.mr_wf.detect_provider")
    def test_explicit_base_overrides(self, mock_provider, _b, _a, _me):
        mock_provider.return_value = _INFO
        with patch("lib.mr_wf.run_claude", return_value=0) as mock_claude:
            run_mr(base="develop")
        prompt = mock_claude.call_args[0][0]
        self.assertIn("develop", prompt)
        self.assertNotIn("--base main", prompt)

    @patch("lib.mr_wf.detect_self_assignee", return_value="")
    @patch("lib.mr_wf.remote_default_branch", return_value="main")
    @patch("lib.mr_wf.current_branch", return_value="feat")
    @patch("lib.mr_wf.detect_provider")
    def test_no_draft(self, mock_provider, _b, _a, _me):
        mock_provider.return_value = _INFO
        with patch("lib.mr_wf.run_claude", return_value=0) as mock_claude:
            run_mr(draft=False)
        prompt = mock_claude.call_args[0][0]
        self.assertNotIn("--draft", prompt)


if __name__ == "__main__":
    unittest.main()

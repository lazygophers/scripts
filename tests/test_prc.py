#!/usr/bin/env python3
"""Tests for lib.prc_wf（PR 查重逻辑）。"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.ai_workflow import ProviderInfo
from lib.prc_wf import _find_existing_pr


def _gh_info() -> ProviderInfo:
    return ProviderInfo(
        provider="gh", host="github.com", repo="owner/repo",
        remote="origin", remote_url="git@github.com:owner/repo.git",
    )


def _glab_info() -> ProviderInfo:
    return ProviderInfo(
        provider="glab", host="gitlab.example.com", repo="owner/repo",
        remote="origin", remote_url="git@gitlab.example.com:owner/repo.git",
    )


class TestFindExistingPrGh(unittest.TestCase):
    @patch("lib.prc_wf.run")
    def test_existing_open_pr_returns_url(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps([{"url": "https://github.com/owner/repo/pull/42"}]), stderr=""
        )
        url = _find_existing_pr(_gh_info(), branch="feat", base="main")
        self.assertEqual(url, "https://github.com/owner/repo/pull/42")

    @patch("lib.prc_wf.run")
    def test_no_open_pr_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        self.assertIsNone(_find_existing_pr(_gh_info(), branch="feat", base="main"))

    @patch("lib.prc_wf.run")
    def test_query_failure_returns_none(self, mock_run):
        """gh 未装/无权限 → rc!=0，静默返回 None 不阻断。"""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="gh not found")
        self.assertIsNone(_find_existing_pr(_gh_info(), branch="feat", base="main"))

    @patch("lib.prc_wf.run")
    def test_invalid_json_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        self.assertIsNone(_find_existing_pr(_gh_info(), branch="feat", base="main"))

    @patch("lib.prc_wf.run")
    def test_gh_uses_correct_filter(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        _find_existing_pr(_gh_info(), branch="feat/x", base="develop")
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0:3], ["gh", "pr", "list"])
        self.assertIn("--head", cmd)
        self.assertIn("feat/x", cmd)
        self.assertIn("--base", cmd)
        self.assertIn("develop", cmd)
        self.assertIn("open", cmd)


class TestFindExistingPrGlab(unittest.TestCase):
    @patch("lib.prc_wf.run")
    def test_existing_mr_returns_url(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Showing 1 open\n!42  feat  Title  https://gitlab.example.com/owner/repo/-/merge_requests/42",
            stderr="",
        )
        url = _find_existing_pr(_glab_info(), branch="feat", base="main")
        self.assertEqual(url, "https://gitlab.example.com/owner/repo/-/merge_requests/42")

    @patch("lib.prc_wf.run")
    def test_no_mr_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="No open MRs", stderr="")
        self.assertIsNone(_find_existing_pr(_glab_info(), branch="feat", base="main"))

    @patch("lib.prc_wf.run")
    def test_glab_uses_correct_filter(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        _find_existing_pr(_glab_info(), branch="feat", base="main")
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0:3], ["glab", "mr", "list"])
        self.assertIn("--source-branch", cmd)
        self.assertIn("--target-branch", cmd)
        self.assertIn("opened", cmd)


class TestRunPrcSkipsExisting(unittest.TestCase):
    """run_prc 已有 open PR 时跳过创建，不调 run_claude。"""

    @patch("lib.prc_wf.run")
    @patch("lib.prc_wf.run_claude")
    @patch("lib.prc_wf.detect_self_assignee", return_value="me")
    @patch("lib.prc_wf.detect_provider")
    def test_skips_when_pr_exists(self, mock_provider, _mock_assignee,
                                  mock_claude, mock_run):
        mock_provider.return_value = _gh_info()
        # 查重命中
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps([{"url": "https://github.com/owner/repo/pull/42"}]), stderr=""
        )
        import lib.prc_wf as prc
        rc = prc.run_prc(base="main")
        self.assertEqual(rc, 0)
        mock_claude.assert_not_called()


if __name__ == "__main__":
    unittest.main()

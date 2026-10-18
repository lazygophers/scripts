#!/usr/bin/env python3
"""Tests for lib.git (fetch_all 命令核心)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.git import fetch_all


def _mock_repos(*names):
    return [Path("/test") / n for n in names]


class TestScanReposCoversNonAsciiNames(unittest.TestCase):
    def test_chinese_dir_is_included(self):
        """中文目录仓库不再被 ASCII 白名单静默跳过（统一走 scan_repos）。"""
        from lib.batch_git import scan_repos

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "中文仓库"
            repo.mkdir()
            (repo / ".git").mkdir()
            repos = scan_repos(root)
        self.assertEqual([r.name for r in repos], ["中文仓库"])


class TestFetchAll(unittest.TestCase):
    @patch("lib.git.retry_command")
    @patch("lib.batch_git.scan_repos")
    def test_single_success(self, mock_list, mock_retry):
        mock_list.return_value = _mock_repos("repo1")
        mock_retry.return_value = MagicMock(ok=True, last_output="Fetched")
        self.assertEqual(fetch_all(), 0)
        mock_retry.assert_called_once()

    @patch("lib.git.retry_command")
    @patch("lib.batch_git.scan_repos")
    def test_multiple_success(self, mock_list, mock_retry):
        mock_list.return_value = _mock_repos("r1", "r2", "r3")
        mock_retry.return_value = MagicMock(ok=True, last_output="")
        self.assertEqual(fetch_all(), 0)
        self.assertEqual(mock_retry.call_count, 3)

    @patch("lib.git.retry_command")
    @patch("lib.batch_git.scan_repos")
    def test_failure(self, mock_list, mock_retry):
        mock_list.return_value = _mock_repos("repo1")
        mock_retry.return_value = MagicMock(ok=False, last_output="network error")
        self.assertEqual(fetch_all(), 1)

    @patch("lib.git.retry_command")
    @patch("lib.batch_git.scan_repos")
    def test_partial_failure(self, mock_list, mock_retry):
        mock_list.return_value = _mock_repos("r1", "r2")
        mock_retry.side_effect = [
            MagicMock(ok=True, last_output=""),
            MagicMock(ok=False, last_output="fail"),
        ]
        self.assertEqual(fetch_all(), 1)

    @patch("lib.git.retry_command")
    @patch("lib.batch_git.scan_repos")
    def test_no_repos(self, mock_list, mock_retry):
        mock_list.return_value = []
        self.assertEqual(fetch_all(), 0)
        mock_retry.assert_not_called()

    @patch("lib.git.retry_command")
    @patch("lib.batch_git.scan_repos")
    def test_retry_params(self, mock_list, mock_retry):
        repo = Path("/test/repo1")
        mock_list.return_value = [repo]
        mock_retry.return_value = MagicMock(ok=True, last_output="")
        fetch_all()
        mock_retry.assert_called_once_with(
            ["git", "fetch", "--all"], cwd=str(repo), max_retries=3, timeout=120
        )

    @patch("lib.git.retry_command")
    @patch("lib.batch_git.scan_repos")
    def test_any_failure_returns_one(self, mock_list, mock_retry):
        mock_list.return_value = _mock_repos("r1")
        mock_retry.return_value = MagicMock(ok=False, last_output="error")
        self.assertEqual(fetch_all(), 1)

    @patch("lib.git.retry_command")
    @patch("lib.batch_git.scan_repos")
    def test_collects_failures(self, mock_list, mock_retry):
        mock_list.return_value = _mock_repos("r1", "r2", "r3")
        mock_retry.side_effect = [
            MagicMock(ok=True, last_output=""),
            MagicMock(ok=False, last_output="e1"),
            MagicMock(ok=False, last_output="e2"),
        ]
        self.assertEqual(fetch_all(), 1)

    @patch("lib.git.retry_command")
    @patch("lib.batch_git.scan_repos")
    def test_output_shows_repo_status(self, mock_list, mock_retry):
        """逐仓库 fetch 状态行走 Reporter，输出含仓库名与状态。"""
        import io
        from contextlib import redirect_stderr

        repo = Path("/test/alpha_repo")
        mock_list.return_value = [repo]
        mock_retry.return_value = MagicMock(ok=False, last_output="net error")
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = fetch_all()
        out = buf.getvalue()
        self.assertEqual(rc, 1)
        self.assertIn("alpha_repo", out)
        # 失败汇总段（rule 标题）+ 失败计数
        self.assertIn("执行结果", out)
        self.assertIn("失败", out)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for lib.git (fetch_all 命令核心)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.git import _list_top_repos, fetch_all


class TestListTopRepos(unittest.TestCase):
    @patch("lib.git.os.listdir")
    def test_single(self, mock_listdir):
        mock_listdir.return_value = ["repo1"]
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "is_dir", return_value=True):
            repos = _list_top_repos(Path("/test"))
        self.assertEqual(len(repos), 1)

    @patch("lib.git.os.listdir")
    def test_multiple(self, mock_listdir):
        mock_listdir.return_value = ["repo1", "repo2", "repo3"]
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "is_dir", return_value=True):
            repos = _list_top_repos(Path("/test"))
        self.assertEqual(len(repos), 3)

    @patch("lib.git.os.listdir")
    def test_skip_hidden(self, mock_listdir):
        mock_listdir.return_value = [".hidden", "repo1", ".git"]

        def git_only(self):
            return str(self).endswith("repo1/.git")

        with patch.object(Path, "exists", git_only), \
             patch.object(Path, "is_dir", return_value=True):
            repos = _list_top_repos(Path("/test"))
        self.assertTrue(all(not r.name.startswith(".") for r in repos))

    @patch("lib.git.os.listdir")
    def test_skip_unsafe_names(self, mock_listdir):
        mock_listdir.return_value = ["repo1", "repo;name", "repo|name", "normal-repo"]

        def git_only(self):
            return str(self).endswith("/.git")

        with patch.object(Path, "exists", git_only), \
             patch.object(Path, "is_dir", return_value=True):
            repos = _list_top_repos(Path("/test"))
        for repo in repos:
            self.assertNotIn(";", repo.name)
            self.assertNotIn("|", repo.name)

    @patch("lib.git.os.listdir")
    def test_no_git_dirs(self, mock_listdir):
        mock_listdir.return_value = ["folder1", "folder2"]
        repos = _list_top_repos(Path("/test"))
        self.assertEqual(len(repos), 0)

    @patch("lib.git.os.listdir")
    def test_includes_git_file_worktree(self, mock_listdir):
        """`.git` 作为文件（worktree/submodule gitdir 指针）也应被识别，与 scan_repos 对齐。"""
        mock_listdir.return_value = ["wt-repo"]

        def git_only(self):
            return str(self).endswith("wt-repo/.git")

        with patch.object(Path, "exists", git_only), \
             patch.object(Path, "is_dir", return_value=True):
            repos = _list_top_repos(Path("/test"))
        self.assertEqual(len(repos), 1)
        self.assertEqual(repos[0].name, "wt-repo")

    @patch("lib.git.os.listdir")
    def test_sorted(self, mock_listdir):
        mock_listdir.return_value = ["z-repo", "a-repo", "m-repo"]

        def git_only(self):
            return str(self).endswith("/.git")

        with patch.object(Path, "exists", git_only), \
             patch.object(Path, "is_dir", return_value=True):
            repos = _list_top_repos(Path("/test"))
        names = [r.name for r in repos]
        self.assertEqual(names, sorted(names))


class TestFetchAll(unittest.TestCase):
    @patch("lib.git.retry_command")
    @patch("lib.git._list_top_repos")
    def test_single_success(self, mock_list, mock_retry):
        mock_list.return_value = [Path("/test/repo1")]
        mock_retry.return_value = MagicMock(ok=True, last_output="Fetched")
        self.assertEqual(fetch_all(), 0)
        mock_retry.assert_called_once()

    @patch("lib.git.retry_command")
    @patch("lib.git._list_top_repos")
    def test_multiple_success(self, mock_list, mock_retry):
        mock_list.return_value = [Path("/test/r1"), Path("/test/r2"), Path("/test/r3")]
        mock_retry.return_value = MagicMock(ok=True, last_output="")
        self.assertEqual(fetch_all(), 0)
        self.assertEqual(mock_retry.call_count, 3)

    @patch("lib.git.retry_command")
    @patch("lib.git._list_top_repos")
    def test_failure(self, mock_list, mock_retry):
        mock_list.return_value = [Path("/test/repo1")]
        mock_retry.return_value = MagicMock(ok=False, last_output="network error")
        self.assertEqual(fetch_all(), 1)

    @patch("lib.git.retry_command")
    @patch("lib.git._list_top_repos")
    def test_partial_failure(self, mock_list, mock_retry):
        mock_list.return_value = [Path("/test/r1"), Path("/test/r2")]
        mock_retry.side_effect = [
            MagicMock(ok=True, last_output=""),
            MagicMock(ok=False, last_output="fail"),
        ]
        self.assertEqual(fetch_all(), 1)

    @patch("lib.git.retry_command")
    @patch("lib.git._list_top_repos")
    def test_no_repos(self, mock_list, mock_retry):
        mock_list.return_value = []
        self.assertEqual(fetch_all(), 0)
        mock_retry.assert_not_called()

    @patch("lib.git.retry_command")
    @patch("lib.git._list_top_repos")
    def test_retry_params(self, mock_list, mock_retry):
        repo = Path("/test/repo1")
        mock_list.return_value = [repo]
        mock_retry.return_value = MagicMock(ok=True, last_output="")
        fetch_all()
        mock_retry.assert_called_once_with(
            ["git", "fetch", "--all"], cwd=str(repo), max_retries=3, timeout=120
        )

    @patch("lib.git.retry_command")
    @patch("lib.git._list_top_repos")
    def test_any_failure_returns_one(self, mock_list, mock_retry):
        mock_list.return_value = [Path("/test/r1")]
        mock_retry.return_value = MagicMock(ok=False, last_output="error")
        self.assertEqual(fetch_all(), 1)

    @patch("lib.git.retry_command")
    @patch("lib.git._list_top_repos")
    def test_collects_failures(self, mock_list, mock_retry):
        mock_list.return_value = [Path("/test/r1"), Path("/test/r2"), Path("/test/r3")]
        mock_retry.side_effect = [
            MagicMock(ok=True, last_output=""),
            MagicMock(ok=False, last_output="e1"),
            MagicMock(ok=False, last_output="e2"),
        ]
        self.assertEqual(fetch_all(), 1)

    @patch("lib.git.retry_command")
    @patch("lib.git._list_top_repos")
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

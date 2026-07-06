#!/usr/bin/env python3
"""Tests for lib.git (find_git_repos 命令核心)."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.git import _walk_repos, _is_suspicious, find_repos_print


class TestIsSuspicious(unittest.TestCase):
    def test_parent_directory(self):
        self.assertTrue(_is_suspicious("../something"))
        self.assertTrue(_is_suspicious("path/../other"))

    def test_dollar_sign(self):
        self.assertTrue(_is_suspicious("path/$VAR/test"))
        self.assertTrue(_is_suspicious("$HOME/test"))

    def test_backtick(self):
        self.assertTrue(_is_suspicious("path/`cmd`/test"))

    def test_safe_paths(self):
        self.assertFalse(_is_suspicious("project/repo"))
        self.assertFalse(_is_suspicious("some-repo"))
        self.assertFalse(_is_suspicious("test_repo"))
        self.assertFalse(_is_suspicious("."))


class TestWalkRepos(unittest.TestCase):
    @patch("lib.git.os.walk")
    def test_single(self, mock_walk):
        mock_walk.return_value = [
            ("/root", ["project1", ".git"], []),
            ("/root/project1", [".git"], []),
        ]
        # Path.resolve 在 _walk_repos 内调用
        with patch.object(Path, "resolve", return_value=Path("/root/project1")):
            repos = _walk_repos(Path("/root"))
        self.assertEqual(len(repos), 2)

    @patch("lib.git.os.walk")
    def test_multiple(self, mock_walk):
        mock_walk.return_value = [
            ("/root", ["project1", "project2"], []),
            ("/root/project1", [".git"], []),
            ("/root/project2", [".git"], []),
        ]
        with patch.object(Path, "resolve", return_value=Path("/x")):
            repos = _walk_repos(Path("/root"))
        self.assertEqual(len(repos), 2)

    @patch("lib.git.os.walk")
    def test_none(self, mock_walk):
        mock_walk.return_value = [
            ("/root", ["folder1", "folder2"], []),
            ("/root/folder1", [], []),
        ]
        repos = _walk_repos(Path("/root"))
        self.assertEqual(len(repos), 0)

    @patch("lib.git.os.walk")
    def test_skip_suspicious(self, mock_walk):
        mock_walk.return_value = [
            ("/root", ["normal", "suspicious"], []),
            ("/root/normal", [".git"], []),
            ("/root/../suspicious", [".git"], []),
        ]
        with patch.object(Path, "resolve", return_value=Path("/x")):
            repos = _walk_repos(Path("/root"))
        self.assertTrue(all("suspicious" not in r[0] for r in repos))

    @patch("lib.git.os.walk")
    def test_stops_at_git_dir(self, mock_walk):
        dirnames = [".git", "subdir"]
        mock_walk.return_value = [("/root/project", dirnames, [])]
        with patch.object(Path, "resolve", return_value=Path("/x")):
            _walk_repos(Path("/root"))
        self.assertNotIn(".git", dirnames)

    @patch("lib.git.os.walk")
    def test_sorted(self, mock_walk):
        mock_walk.return_value = [
            ("/root", ["z_repo", "a_repo", "m_repo"], []),
            ("/root/z_repo", [".git"], []),
            ("/root/a_repo", [".git"], []),
            ("/root/m_repo", [".git"], []),
        ]
        with patch.object(Path, "resolve", return_value=Path("/x")):
            repos = _walk_repos(Path("/root"))
        self.assertEqual(len(repos), 3)
        rel_paths = [r[0] for r in repos]
        self.assertEqual(rel_paths, sorted(rel_paths))

    @patch("lib.git.os.walk")
    def test_resolve_exception_skipped(self, mock_walk):
        mock_walk.return_value = [
            ("/root", ["broken_link"], []),
            ("/root/broken_link", [".git"], []),
        ]
        original_resolve = Path.resolve

        def mock_resolve(self):
            if "broken_link" in str(self):
                raise OSError("Broken symlink")
            return original_resolve(self)

        with patch.object(Path, "resolve", mock_resolve):
            repos = _walk_repos(Path("/root"))
        self.assertTrue(all("broken_link" not in r[0] for r in repos))


class TestFindReposPrint(unittest.TestCase):
    @patch("lib.git._walk_repos")
    def test_with_repos(self, mock_walk):
        mock_walk.return_value = [
            ("project1", "/abs/path/project1"),
            ("project2", "/abs/path/project2"),
        ]
        self.assertEqual(find_repos_print(), 0)

    @patch("lib.git._walk_repos")
    def test_no_repos(self, mock_walk):
        mock_walk.return_value = []
        self.assertEqual(find_repos_print(), 0)

    @patch("lib.git._walk_repos")
    def test_output_lists_repo_names(self, mock_walk):
        """Reporter 路径（TTY）或 markdown 路径（非 TTY）都应输出仓库名。"""
        import io
        from contextlib import redirect_stderr, redirect_stdout

        mock_walk.return_value = [("alpha_repo", "/abs/alpha_repo")]
        err_buf, out_buf = io.StringIO(), io.StringIO()
        with redirect_stderr(err_buf), redirect_stdout(out_buf):
            find_repos_print()
        combined = err_buf.getvalue() + out_buf.getvalue()
        self.assertIn("alpha_repo", combined)
        # Reporter 摘要行（纯文本降级路径也应含“仓库”字样）
        self.assertIn("仓库", combined)


if __name__ == "__main__":
    unittest.main()

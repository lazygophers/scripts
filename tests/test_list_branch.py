#!/usr/bin/env python3
"""Tests for lib.git (list_branch 分支总览)."""
import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.git import (
    _REF_SEP,
    _collect_all_branches,
    _parse_branch_refs,
    _render_branch_table,
    list_branch,
)


def _ref_line(name, head=" ", sha="abc1234", date="2026-07-17",
              upstream="", track=""):
    """构造一条 for-each-ref 格式输出行。"""
    return _REF_SEP.join([name, head, sha, date, upstream, track])


class TestParseBranchRefs(unittest.TestCase):
    @patch("lib.git.run")
    def test_parses_single_branch(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=_ref_line("master", head="*"), stderr="")
        branches = _parse_branch_refs("/repo")
        self.assertEqual(len(branches), 1)
        self.assertEqual(branches[0]["name"], "master")
        self.assertTrue(branches[0]["current"])
        self.assertEqual(branches[0]["sha"], "abc1234")

    @patch("lib.git.run")
    def test_parses_multiple(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="\n".join([
                _ref_line("main", head="*"),
                _ref_line("dev", upstream="origin/dev", track="[ahead 2]"),
                _ref_line("feat/x"),
            ]),
            stderr="")
        branches = _parse_branch_refs("/repo")
        self.assertEqual(len(branches), 3)
        self.assertTrue(branches[0]["current"])
        self.assertFalse(branches[1]["current"])
        self.assertEqual(branches[1]["upstream"], "origin/dev")
        self.assertEqual(branches[1]["track"], "[ahead 2]")
        self.assertEqual(branches[2]["name"], "feat/x")

    @patch("lib.git.run")
    def test_empty_repo_no_branches(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        self.assertEqual(_parse_branch_refs("/repo"), [])

    @patch("lib.git.run")
    def test_malformed_line_skipped(self, mock_run):
        """字段不足 6 的行（如分隔符缺失）应跳过，不崩。"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="only|three|fields\n" + _ref_line("ok"),
            stderr="")
        branches = _parse_branch_refs("/repo")
        self.assertEqual(len(branches), 1)
        self.assertEqual(branches[0]["name"], "ok")


class TestCollectAllBranches(unittest.TestCase):
    @patch("lib.git._parse_branch_refs")
    def test_collects_across_repos(self, mock_parse):
        mock_parse.side_effect = [
            [{"name": "master", "current": True, "sha": "a", "date": "d",
              "upstream": "origin/master", "track": ""}],
            [{"name": "master", "current": False, "sha": "b", "date": "d",
              "upstream": "", "track": ""}],
        ]
        root = Path("/root")
        repos = [root / "r1", root / "r2"]
        rows = _collect_all_branches(repos, root)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "r1")
        self.assertEqual(rows[1][0], "r2")

    @patch("lib.git._parse_branch_refs")
    def test_single_repo_display(self, mock_parse):
        """root 自身即仓库时 display 用 repo.name（相对路径为 '.'）。"""
        mock_parse.return_value = [
            {"name": "main", "current": True, "sha": "x", "date": "d",
             "upstream": "", "track": ""}]
        root = Path("/root")
        rows = _collect_all_branches([root], root)
        self.assertEqual(rows[0][0], root.name)


class TestRenderBranchTable(unittest.TestCase):
    def _plain_reporter(self):
        from lib.ui import reporter
        r = reporter(stderr=True)
        r.console = None  # 走纯文本路径
        return r

    def _rows(self):
        return [
            ("repoA", {"name": "master", "current": True, "sha": "abc1234",
                       "date": "2026-07-17", "upstream": "origin/master", "track": ""}),
            ("repoA", {"name": "dev", "current": False, "sha": "def5678",
                       "date": "2026-07-10", "upstream": "", "track": "[ahead 2]"}),
            ("repoB", {"name": "master", "current": False, "sha": "xyz9012",
                       "date": "2026-07-15", "upstream": "origin/master",
                       "track": "[behind 3]"}),
        ]

    def test_plain_renders_all_rows(self):
        r = self._plain_reporter()
        buf = io.StringIO()
        with redirect_stderr(buf):
            _render_branch_table(r, self._rows(), mark_duplicates=True)
        out = buf.getvalue()
        self.assertIn("repoA", out)
        self.assertIn("repoB", out)
        self.assertIn("master", out)
        self.assertIn("dev", out)
        self.assertIn("abc1234", out)

    def test_plain_marks_duplicates(self):
        """跨仓库同名分支（master 出现 2 次）标 ⟱。"""
        r = self._plain_reporter()
        buf = io.StringIO()
        with redirect_stderr(buf):
            _render_branch_table(r, self._rows(), mark_duplicates=True)
        out = buf.getvalue()
        self.assertIn("⟱", out)
        self.assertIn("跨仓库重复分支名", out)
        # dev 仅 1 次 → 不标
        dev_line = next(ln for ln in out.splitlines() if "dev" in ln)
        self.assertNotIn("⟱", dev_line)

    def test_plain_no_duplicate_mark_when_disabled(self):
        r = self._plain_reporter()
        buf = io.StringIO()
        with redirect_stderr(buf):
            _render_branch_table(r, self._rows(), mark_duplicates=False)
        out = buf.getvalue()
        self.assertNotIn("⟱", out)

    def test_plain_track_without_upstream(self):
        """有 track 无 upstream（孤儿 tracking）单独显示 [track]。"""
        r = self._plain_reporter()
        rows = [
            ("r", {"name": "feat", "current": True, "sha": "a", "date": "d",
                   "upstream": "", "track": "[gone]"}),
        ]
        buf = io.StringIO()
        with redirect_stderr(buf):
            _render_branch_table(r, rows, mark_duplicates=False)
        out = buf.getvalue()
        self.assertIn("[gone]", out)

    def test_rich_path_does_not_crash(self):
        """Rich 可用时（console 非 None）渲染 Table 不崩。"""
        from lib.ui import reporter
        r = reporter(stderr=True)
        if r.console is None:
            self.skipTest("Rich 不可用，跳过 Rich 路径测试")
        buf = io.StringIO()
        r.console.file = buf  # 重定向 Rich 输出到 buffer
        _render_branch_table(r, self._rows(), mark_duplicates=True)
        out = buf.getvalue()
        self.assertIn("repoA", out)
        self.assertIn("master", out)


class TestListBranches(unittest.TestCase):
    @patch("lib.git._collect_all_branches")
    @patch("lib.git._render_branch_table")
    @patch("lib.git.reporter")
    def test_single_repo_uses_root_only(self, mock_rep, mock_render, mock_collect):
        """root/.git 存在 → 仅列该仓（不调 scan_repos）。"""
        mock_r = MagicMock()
        mock_rep.return_value = mock_r
        with patch.object(Path, "exists", return_value=True):
            rc = list_branch(Path("/repo"))
        self.assertEqual(rc, 0)
        mock_collect.assert_called_once()
        repos_arg = mock_collect.call_args[0][0]
        self.assertEqual(repos_arg, [Path("/repo").resolve()])

    @patch("lib.batch_git.scan_repos")
    @patch("lib.git._collect_all_branches")
    @patch("lib.git._render_branch_table")
    @patch("lib.git.reporter")
    def test_multi_repo_scans(self, mock_rep, mock_render, mock_collect, mock_scan):
        """root 无 .git → 扫描子目录所有仓库。"""
        mock_r = MagicMock()
        mock_rep.return_value = mock_r
        found = [Path("/root/a"), Path("/root/b")]
        mock_scan.return_value = found
        with patch.object(Path, "exists", return_value=False):
            rc = list_branch(Path("/root"))
        self.assertEqual(rc, 0)
        mock_scan.assert_called_once()
        mock_collect.assert_called_once_with(found, Path("/root").resolve())

    @patch("lib.batch_git.scan_repos")
    @patch("lib.git.reporter")
    def test_no_repos_returns_zero(self, mock_rep, mock_scan):
        """无仓库 → 提示并 exit 0（非失败）。"""
        mock_r = MagicMock()
        mock_rep.return_value = mock_r
        mock_scan.return_value = []
        with patch.object(Path, "exists", return_value=False):
            rc = list_branch(Path("/empty"))
        self.assertEqual(rc, 0)

    @patch("lib.git._collect_all_branches")
    @patch("lib.git._render_branch_table")
    @patch("lib.git.reporter")
    def test_multi_repo_marks_duplicates(self, mock_rep, mock_render, mock_collect):
        """多仓时 mark_duplicates=True（触发跨仓重复标注）。"""
        mock_r = MagicMock()
        mock_rep.return_value = mock_r
        mock_collect.return_value = []
        with patch.object(Path, "exists", return_value=False):
            with patch("lib.batch_git.scan_repos", return_value=[Path("/r1"), Path("/r2")]):
                list_branch(Path("/root"))
        _, kwargs = mock_render.call_args
        self.assertTrue(kwargs["mark_duplicates"])

    @patch("lib.git._collect_all_branches")
    @patch("lib.git._render_branch_table")
    @patch("lib.git.reporter")
    def test_single_repo_no_duplicate_mark(self, mock_rep, mock_render, mock_collect):
        """单仓时 mark_duplicates=False（无跨仓比较意义）。"""
        mock_r = MagicMock()
        mock_rep.return_value = mock_r
        with patch.object(Path, "exists", return_value=True):
            list_branch(Path("/repo"))
        _, kwargs = mock_render.call_args
        self.assertFalse(kwargs["mark_duplicates"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for lib/cli/gitwf.py 分派：here / all / auto 三个子命令 + 12 个入口函数。"""
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.cli import gitwf

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "bin"


class TestEntryFunctions(unittest.TestCase):
    """每个入口函数把 (name, action, target) 显式传给 _run。"""

    def test_all_entry_names_present(self):
        expected = {
            "merge_branch", "merge_canary", "merge_dev", "merge_develop", "merge_master", "merge_test",
            "push_branch", "push_canary", "push_dev", "push_develop", "push_master", "push_test",
        }
        for name in expected:
            self.assertTrue(callable(getattr(gitwf, name, None)), f"缺少入口函数 {name}")

    def test_action_target_pairs(self):
        cases = {
            "merge_canary": ("merge", "canary"),
            "merge_master": ("merge", "master"),
            "merge_dev": ("merge", "dev"),
            "push_develop": ("push", "develop"),
            "push_dev": ("push", "dev"),
            "push_test": ("push", "test"),
            "merge_branch": ("merge", None),
            "push_branch": ("push", None),
        }
        for name, (action, target) in cases.items():
            with patch.object(gitwf, "_run") as m_run:
                getattr(gitwf, name)()
            m_run.assert_called_once_with(name, action, target)


class TestDispatchHere(unittest.TestCase):
    """`here` 子命令 → 单仓 merge_to / push_to"""

    @patch("lib.git_workflow.merge_to", return_value=0)
    def test_here_merge(self, mock_merge):
        cli = gitwf.GitWfCli("merge_canary", "merge", "canary")
        with patch("sys.argv", ["merge_canary"]):
            rc = cli.here()
        self.assertEqual(rc, 0)
        mock_merge.assert_called_once()
        self.assertEqual(mock_merge.call_args[0][0], "canary")

    @patch("lib.git_workflow.push_to", return_value=0)
    def test_here_push(self, mock_push):
        cli = gitwf.GitWfCli("push_develop", "push", "develop")
        with patch("sys.argv", ["push_develop"]):
            rc = cli.here()
        self.assertEqual(rc, 0)
        mock_push.assert_called_once()
        self.assertEqual(mock_push.call_args[0][0], "develop")


class TestDispatchAll(unittest.TestCase):
    """`all` 子命令 → 批量 merge_all / push_all"""

    @patch("lib.batch_git.merge_all", return_value=0)
    def test_all_merge(self, mock_merge_all):
        cli = gitwf.GitWfCli("merge_master", "merge", "master")
        with patch("sys.argv", ["merge_master"]):
            rc = cli.all()
        self.assertEqual(rc, 0)
        mock_merge_all.assert_called_once()
        self.assertEqual(mock_merge_all.call_args[0][0], "master")

    @patch("lib.batch_git.push_all", return_value=0)
    def test_all_push(self, mock_push_all):
        cli = gitwf.GitWfCli("push_test", "push", "test")
        with patch("sys.argv", ["push_test"]):
            rc = cli.all()
        self.assertEqual(rc, 0)
        mock_push_all.assert_called_once()
        self.assertEqual(mock_push_all.call_args[0][0], "test")


class TestDispatchAuto(unittest.TestCase):
    """`auto` 子命令 → 按 cwd 是否有 .git 派发 here/all"""

    @patch("lib.git_workflow.merge_to", return_value=0)
    def test_auto_in_repo_calls_here(self, mock_merge):
        cli = gitwf.GitWfCli("merge_canary", "merge", "canary")
        with patch("sys.argv", ["merge_canary"]), \
             patch.object(gitwf.pathlib.Path, "cwd", return_value=REPO_ROOT):
            rc = cli.auto()
        self.assertEqual(rc, 0)
        mock_merge.assert_called_once()

    @patch("lib.batch_git.push_all", return_value=0)
    def test_auto_outside_repo_calls_all(self, mock_push_all):
        cli = gitwf.GitWfCli("push_test", "push", "test")
        with patch("sys.argv", ["push_test"]), \
             patch.object(gitwf.pathlib.Path, "cwd", return_value=Path("/tmp")):
            rc = cli.auto()
        self.assertEqual(rc, 0)
        mock_push_all.assert_called_once()


class TestBareCall(unittest.TestCase):
    """裸调用 `merge_*` 等同 `auto`。"""

    def test_bare_call_delegates_to_auto(self):
        cli = gitwf.GitWfCli("merge_canary", "merge", "canary")
        with patch.object(cli, "auto", return_value=0) as m_auto:
            self.assertEqual(cli(auto_commit=True), 0)
        m_auto.assert_called_once_with(auto_commit=True)

    @patch("lib.git_workflow.merge_to", return_value=0)
    def test_auto_commit_appends_flag(self, mock_merge):
        cli = gitwf.GitWfCli("merge_canary", "merge", "canary")
        with patch("sys.argv", ["merge_canary"]):
            cli.here(auto_commit=True)
        self.assertIn("--auto-commit", mock_merge.call_args[0][1])


class TestPushTargetsDispatched(unittest.TestCase):
    """验证所有 push_* 入口目标分支正确派发"""

    @patch("lib.git_workflow.push_to")
    def test_push_targets(self, mock_push):
        for name, target in [("push_canary", "canary"),
                             ("push_dev", "dev"),
                             ("push_develop", "develop"),
                             ("push_master", "master"),
                             ("push_test", "test")]:
            mock_push.reset_mock()
            cli = gitwf.GitWfCli(name, "push", target)
            with patch("sys.argv", [name]):
                cli.here()
            self.assertEqual(mock_push.call_args[0][0], target, name)


class TestBranchEntry(unittest.TestCase):
    """merge_branch/push_branch：分支名必填，由 _pop_branch_arg 从 argv 摘出。"""

    @patch("lib.git_workflow.push_to", return_value=0)
    def test_branch_arg_dispatches(self, mock_push):
        cli = gitwf.GitWfCli("push_branch", "push", "feature/x")
        with patch("sys.argv", ["push_branch"]):
            self.assertEqual(cli.here(), 0)
        mock_push.assert_called_once()
        self.assertEqual(mock_push.call_args[0][0], "feature/x")

    def test_missing_branch_arg_returns_2(self):
        cli = gitwf.GitWfCli("merge_branch", "merge", "")
        with patch("sys.argv", ["merge_branch"]), \
             patch.object(cli, "_r") as m_r:
            self.assertEqual(cli.here(), 2)
        self.assertIn("分支名", m_r.err.call_args[0][0])

    def test_pop_branch_arg_takes_first_positional(self):
        with patch("sys.argv", ["merge_branch", "feature/x", "here"]):
            self.assertEqual(gitwf._pop_branch_arg("merge_branch"), "feature/x")

    def test_pop_branch_arg_missing_exits_2(self):
        with patch("sys.argv", ["merge_branch"]):
            with self.assertRaises(SystemExit) as cm:
                gitwf._pop_branch_arg("merge_branch")
        self.assertEqual(cm.exception.code, 2)

    def test_no_args_exits_2_with_usage(self):
        import subprocess
        r = subprocess.run([str(BIN_DIR / "merge_branch")],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertIn("用法", r.stderr)


if __name__ == "__main__":
    unittest.main()

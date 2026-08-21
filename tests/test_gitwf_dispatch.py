#!/usr/bin/env python3
"""Tests for bin/_gitwf 分派：fire 重构后用 here / all / auto 三个子命令。"""
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
GITWF_PATH = REPO_ROOT / "bin" / "_gitwf"

# bin/ 不在 package 内，且文件名以 _ 开头不在 importlib 默认后缀表内，
# 用 SourceFileLoader 直接按路径加载。
_gitwf = SourceFileLoader("_gitwf_test_mod", str(GITWF_PATH)).load_module()


class TestNameMap(unittest.TestCase):
    def test_all_new_names_present(self):
        expected = {
            "merge_canary", "merge_develop", "merge_master", "merge_test",
            "push_canary", "push_develop", "push_master", "push_test",
        }
        self.assertEqual(set(_gitwf._NAME_MAP), expected)

    def test_action_target_pairs(self):
        cases = {
            "merge_canary": ("merge", "canary"),
            "merge_master": ("merge", "master"),
            "push_develop": ("push", "develop"),
            "push_test": ("push", "test"),
        }
        for name, expected in cases.items():
            self.assertEqual(_gitwf._NAME_MAP[name], expected, f"{name} mapping")


class TestDispatchHere(unittest.TestCase):
    """`here` 子命令 → 单仓 merge_to / push_to"""

    @patch("lib.git_workflow.merge_to", return_value=0)
    def test_here_merge(self, mock_merge):
        cli = _gitwf.GitWfCli()
        with patch("sys.argv", ["merge_canary"]):
            rc = cli.here()
        self.assertEqual(rc, 0)
        mock_merge.assert_called_once()
        self.assertEqual(mock_merge.call_args[0][0], "canary")

    @patch("lib.git_workflow.push_to", return_value=0)
    def test_here_push(self, mock_push):
        cli = _gitwf.GitWfCli()
        with patch("sys.argv", ["push_develop"]):
            rc = cli.here()
        self.assertEqual(rc, 0)
        mock_push.assert_called_once()
        self.assertEqual(mock_push.call_args[0][0], "develop")


class TestDispatchAll(unittest.TestCase):
    """`all` 子命令 → 批量 merge_all / push_all"""

    @patch("lib.batch_git.merge_all", return_value=0)
    def test_all_merge(self, mock_merge_all):
        cli = _gitwf.GitWfCli()
        with patch("sys.argv", ["merge_master"]):
            rc = cli.all()
        self.assertEqual(rc, 0)
        mock_merge_all.assert_called_once()
        self.assertEqual(mock_merge_all.call_args[0][0], "master")

    @patch("lib.batch_git.push_all", return_value=0)
    def test_all_push(self, mock_push_all):
        cli = _gitwf.GitWfCli()
        with patch("sys.argv", ["push_test"]):
            rc = cli.all()
        self.assertEqual(rc, 0)
        mock_push_all.assert_called_once()
        self.assertEqual(mock_push_all.call_args[0][0], "test")


class TestDispatchAuto(unittest.TestCase):
    """`auto` 子命令 → 按 cwd 是否有 .git 派发 here/all"""

    @patch("lib.git_workflow.merge_to", return_value=0)
    def test_auto_in_repo_calls_here(self, mock_merge):
        cli = _gitwf.GitWfCli()
        with patch("sys.argv", ["merge_canary"]), \
             patch.object(_gitwf.pathlib.Path, "cwd", return_value=REPO_ROOT):
            rc = cli.auto()
        self.assertEqual(rc, 0)
        mock_merge.assert_called_once()

    @patch("lib.batch_git.push_all", return_value=0)
    def test_auto_outside_repo_calls_all(self, mock_push_all):
        cli = _gitwf.GitWfCli()
        with patch("sys.argv", ["push_test"]), \
             patch.object(_gitwf.pathlib.Path, "cwd", return_value=Path("/tmp")):
            rc = cli.auto()
        self.assertEqual(rc, 0)
        mock_push_all.assert_called_once()


class TestPushTargetsDispatched(unittest.TestCase):
    """验证所有 push_* 入口目标分支正确派发"""

    @patch("lib.git_workflow.push_to")
    def test_push_targets(self, mock_push):
        for name, target in [("push_canary", "canary"),
                             ("push_develop", "develop"),
                             ("push_master", "master"),
                             ("push_test", "test")]:
            mock_push.reset_mock()
            cli = _gitwf.GitWfCli()
            with patch("sys.argv", [name]):
                cli.here()
            self.assertEqual(mock_push.call_args[0][0], target, name)


if __name__ == "__main__":
    unittest.main()

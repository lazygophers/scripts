#!/usr/bin/env python3
"""Tests for bin/_gitwf 分派：新名 _NAME_MAP + 单仓/批量自动识别。"""
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
            "merge_canary", "merge_develop", "merge_auto", "merge_test",
            "push_canary", "push_develop", "push_auto", "push_test",
        }
        self.assertEqual(set(_gitwf._NAME_MAP), expected)

    def test_old_names_absent(self):
        for old in ("mergec", "mergedev", "mergem", "merget",
                    "pushc", "pushdev", "pushm", "pusht"):
            self.assertNotIn(old, _gitwf._NAME_MAP)

    def test_action_target_pairs(self):
        cases = {
            "merge_canary": ("merge", "canary"),
            "merge_auto": ("merge", "auto"),
            "push_develop": ("push", "develop"),
            "push_test": ("push", "test"),
        }
        for name, expected in cases.items():
            self.assertEqual(_gitwf._NAME_MAP[name], expected, f"{name} mapping")


class TestUnknownName(unittest.TestCase):
    """未知入口名返回 2。"""

    def test_unknown_returns_2(self):
        with patch("sys.argv", ["_gitwf"]):
            rc = _gitwf.main()
        self.assertEqual(rc, 2)


class TestDispatchAutoDetect(unittest.TestCase):
    """单仓/批量自动识别：根据 cwd 下 .git 是否存在。"""

    def _run_with_argv(self, name: str, in_git_repo: bool):
        with patch("sys.argv", [name]):
            with patch.object(_gitwf.pathlib.Path, "cwd",
                              return_value=REPO_ROOT if in_git_repo else Path("/tmp")):
                return _gitwf.main()

    @patch("lib.git_workflow.merge_to", return_value=0)
    def test_merge_in_git_repo_calls_merge_to(self, mock_merge):
        rc = self._run_with_argv("merge_canary", in_git_repo=True)
        self.assertEqual(rc, 0)
        mock_merge.assert_called_once()
        args, _ = mock_merge.call_args
        self.assertEqual(args[0], "canary")

    @patch("lib.git_workflow.push_to", return_value=0)
    def test_push_in_git_repo_calls_push_to(self, mock_push):
        rc = self._run_with_argv("push_develop", in_git_repo=True)
        self.assertEqual(rc, 0)
        mock_push.assert_called_once()
        self.assertEqual(mock_push.call_args[0][0], "develop")

    @patch("lib.git_workflow.merge_to")
    def test_merge_in_non_git_repo_errors(self, mock_merge):
        rc = self._run_with_argv("merge_canary", in_git_repo=False)
        self.assertEqual(rc, 2)
        mock_merge.assert_not_called()

    @patch("lib.batch_git.push_all", return_value=0)
    def test_push_in_non_git_repo_calls_push_all(self, mock_push_all):
        rc = self._run_with_argv("push_test", in_git_repo=False)
        self.assertEqual(rc, 0)
        mock_push_all.assert_called_once()
        # 第一个参数是 target
        self.assertEqual(mock_push_all.call_args[0][0], "test")

    @patch("lib.git_workflow.push_to")
    def test_push_targets_dispatched(self, mock_push):
        for name, target in [("push_canary", "canary"),
                             ("push_develop", "develop"),
                             ("push_auto", "auto"),
                             ("push_test", "test")]:
            mock_push.reset_mock()
            with patch("sys.argv", [name]), \
                 patch.object(_gitwf.pathlib.Path, "cwd", return_value=REPO_ROOT):
                _gitwf.main()
            self.assertEqual(mock_push.call_args[0][0], target)


if __name__ == "__main__":
    unittest.main()

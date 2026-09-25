#!/usr/bin/env python3
"""薄壳 CLI 的分派契约：裸调用怎么落到子命令、参数怎么传、缺参数返回什么。

黑盒冒烟（tests/test_shells_blackbox.py）只验 `--help` 能退出 0，进不到
分派逻辑；这些薄壳的 coverage 因此一直是 0%。这里在进程内直接调类方法，
把底层 lib 函数 mock 掉，只看 CLI 这一层传了什么。
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from lib.cli.commit import CommitCli
from lib.cli.kk import KkCli
from lib.cli.kkp import KkpCli
from lib.cli.n import NCli


class TestKkCli(unittest.TestCase):
    def test_bare_call_goes_to_by_name(self):
        with patch("lib.cli.kk.kill_by_name", return_value=0) as killer:
            self.assertEqual(KkCli()("python", "node"), 0)
        patterns, = killer.call_args.args
        self.assertEqual(patterns, ["python", "node"])

    def test_dry_run_reaches_the_library(self):
        with patch("lib.cli.kk.kill_by_name", return_value=0) as killer:
            KkCli().by_name("python", dry_run=True)
        self.assertIs(killer.call_args.kwargs["dry_run"], True)

    def test_script_markers_include_kk_so_it_never_kills_itself(self):
        with patch("lib.cli.kk.kill_by_name", return_value=0) as killer:
            KkCli().by_name("python")
        self.assertIn("kk", killer.call_args.kwargs["script_markers"])

    def test_no_pattern_is_an_error_without_touching_any_process(self):
        with patch("lib.cli.kk.kill_by_name") as killer:
            self.assertEqual(KkCli()(), 1)
        killer.assert_not_called()


class TestKkpCli(unittest.TestCase):
    def test_bare_call_parses_the_port(self):
        with patch("lib.cli.kkp.kill_by_port", return_value=0) as killer:
            self.assertEqual(KkpCli()("8080"), 0)
        self.assertEqual(killer.call_args.args[0], "8080")

    def test_dry_run_reaches_the_library(self):
        with patch("lib.cli.kkp.kill_by_port", return_value=0) as killer:
            KkpCli().by_port(8080, dry_run=True)
        self.assertIs(killer.call_args.kwargs["dry_run"], True)

    def test_no_port_is_an_error_without_touching_any_process(self):
        with patch("lib.cli.kkp.kill_by_port") as killer:
            self.assertEqual(KkpCli()(), 1)
        killer.assert_not_called()


class TestNCli(unittest.TestCase):
    def test_bare_call_joins_every_word_into_one_utterance(self):
        with patch("lib.cli.n.say_content", return_value=0) as say:
            self.assertEqual(NCli()("编译", "完成"), 0)
        say.assert_called_once_with("编译 完成")

    def test_empty_content_is_an_error_without_speaking(self):
        with patch("lib.cli.n.say_content") as say:
            self.assertEqual(NCli()(), 1)
        say.assert_not_called()


class TestCommitCli(unittest.TestCase):
    """`commit` 的分支：cwd 是 git 仓库走单仓，否则批量扫子目录。"""

    def test_auto_picks_the_single_repo_path_inside_a_repo(self):
        with patch("lib.cli.commit.run_commit", return_value=0) as single, \
                patch("lib.cli.commit.commit_all") as batch, \
                patch("pathlib.Path.exists", return_value=True):
            self.assertEqual(CommitCli()("修好了"), 0)
        single.assert_called_once()
        self.assertEqual(single.call_args.args[0], "修好了")
        batch.assert_not_called()

    def test_auto_falls_back_to_the_batch_path_outside_a_repo(self):
        with patch("lib.cli.commit.run_commit") as single, \
                patch("lib.cli.commit.commit_all", return_value=0) as batch, \
                patch("pathlib.Path.exists", return_value=False):
            self.assertEqual(CommitCli()(), 0)
        batch.assert_called_once()
        self.assertIsNone(batch.call_args.kwargs["msg"])
        single.assert_not_called()

    def test_here_and_all_stay_explicit_regardless_of_cwd(self):
        with patch("lib.cli.commit.run_commit", return_value=0) as single, \
                patch("lib.cli.commit.commit_all", return_value=0) as batch, \
                patch("pathlib.Path.exists", return_value=False):
            CommitCli().here("msg")
            single.assert_called_once()
            batch.assert_not_called()
        with patch("lib.cli.commit.run_commit") as single, \
                patch("lib.cli.commit.commit_all", return_value=0) as batch, \
                patch("pathlib.Path.exists", return_value=True):
            CommitCli().all("msg")
            batch.assert_called_once()
            single.assert_not_called()

    def test_settings_file_only_reaches_the_single_repo_path(self):
        with patch("lib.cli.commit.run_commit", return_value=0) as single, \
                patch("pathlib.Path.exists", return_value=True):
            CommitCli()("msg", settings="/tmp/settings.yaml")
        self.assertEqual(single.call_args.kwargs["settings_file"], "/tmp/settings.yaml")


if __name__ == "__main__":
    unittest.main()

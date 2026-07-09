#!/usr/bin/env python3
"""Tests for lib.ai_workflow (provider 检测 / URL 解析 / branch / 默认分支)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.ai_workflow import (
    ProviderInfo,
    _normalize_host,
    current_branch,
    detect_provider,
    detect_self_assignee,
    git_toplevel,
    parse_remote_url,
    primary_remote,
    remote_default_branch,
)


class TestNormalizeHost(unittest.TestCase):
    def test_ssh_gitlab(self):
        self.assertEqual(_normalize_host("ssh.gitlab.starpago.com"), "gitlab.starpago.com")

    def test_ssh_github(self):
        self.assertEqual(_normalize_host("ssh.github.com"), "github.com")

    def test_plain(self):
        self.assertEqual(_normalize_host("gitlab.com"), "gitlab.com")


class TestParseRemoteUrl(unittest.TestCase):
    def test_ssh_protocol(self):
        self.assertEqual(
            parse_remote_url("ssh://git@gitlab.starpago.com/group/proj.git"),
            ("gitlab.starpago.com", "group/proj"),
        )

    def test_ssh_port(self):
        self.assertEqual(
            parse_remote_url("ssh://git@example.com:2222/o/r.git"),
            ("example.com", "o/r"),
        )

    def test_git_at(self):
        self.assertEqual(
            parse_remote_url("git@github.com:owner/repo.git"),
            ("github.com", "owner/repo"),
        )

    def test_git_at_no_dotgit(self):
        self.assertEqual(
            parse_remote_url("git@github.com:owner/repo"),
            ("github.com", "owner/repo"),
        )

    def test_https(self):
        self.assertEqual(
            parse_remote_url("https://github.com/owner/repo.git"),
            ("github.com", "owner/repo"),
        )

    def test_https_with_token(self):
        self.assertEqual(
            parse_remote_url("https://token@github.com/o/r.git"),
            ("github.com", "o/r"),
        )

    def test_ssh_github_alias(self):
        self.assertEqual(
            parse_remote_url("ssh://git@ssh.github.com/o/r.git"),
            ("github.com", "o/r"),
        )

    def test_invalid(self):
        self.assertIsNone(parse_remote_url("not-a-url"))
        self.assertIsNone(parse_remote_url(""))


class TestGitToplevel(unittest.TestCase):
    def test_in_repo(self):
        # 测试自身在 git 仓库内运行
        result = git_toplevel()
        self.assertIsNotNone(result)

    @patch("lib.ai_workflow.run")
    def test_not_in_repo(self, mock_run):
        mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="")
        self.assertIsNone(git_toplevel())

    @patch("lib.ai_workflow.run")
    def test_empty_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        self.assertIsNone(git_toplevel())


class TestCurrentBranch(unittest.TestCase):
    @patch("lib.ai_workflow.run")
    def test_normal(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="feature/x\n", stderr="")
        self.assertEqual(current_branch(), "feature/x")

    @patch("lib.ai_workflow.run")
    def test_detached(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        self.assertEqual(current_branch(), "detached")


class TestPrimaryRemote(unittest.TestCase):
    @patch("lib.ai_workflow.run")
    @patch("lib.ai_workflow.current_branch", return_value="main")
    def test_branch_configured_remote(self, _mock_branch, mock_run):
        # 第一次 run = git config branch.main.remote 成功
        mock_run.return_value = MagicMock(returncode=0, stdout="origin\n", stderr="")
        self.assertEqual(primary_remote(), "origin")

    @patch("lib.ai_workflow.run")
    @patch("lib.ai_workflow.current_branch", return_value="main")
    def test_fallback_first_remote(self, _mock_branch, mock_run):
        # config 失败 → git remote 列表
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="upstream\norigin\n", stderr=""),
        ]
        self.assertEqual(primary_remote(), "upstream")

    @patch("lib.ai_workflow.run")
    @patch("lib.ai_workflow.current_branch", return_value="detached")
    def test_no_remotes(self, _mock_branch, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        self.assertIsNone(primary_remote())


class TestDetectProvider(unittest.TestCase):
    @patch("lib.ai_workflow.primary_remote", return_value="origin")
    @patch("lib.ai_workflow.run")
    def test_github(self, mock_run, _mock_remote):
        # primary_remote 内已 mock, 但 run 用于 remote get-url
        mock_run.return_value = MagicMock(returncode=0,
                                          stdout="git@github.com:o/r.git\n", stderr="")
        info = detect_provider()
        self.assertIsNotNone(info)
        self.assertEqual(info.provider, "gh")
        self.assertEqual(info.host, "github.com")
        self.assertEqual(info.repo, "o/r")

    @patch("lib.ai_workflow.primary_remote", return_value="origin")
    @patch("lib.ai_workflow.run")
    def test_gitlab(self, mock_run, _mock_remote):
        mock_run.return_value = MagicMock(returncode=0,
                                          stdout="ssh://git@gitlab.starpago.com/g/p.git\n", stderr="")
        info = detect_provider()
        self.assertIsNotNone(info)
        self.assertEqual(info.provider, "glab")
        self.assertEqual(info.host, "gitlab.starpago.com")

    @patch("lib.ai_workflow.primary_remote", return_value=None)
    def test_no_remote(self, _mock_remote):
        self.assertIsNone(detect_provider())

    @patch("lib.ai_workflow.primary_remote", return_value="origin")
    @patch("lib.ai_workflow.run")
    def test_unparseable_url(self, mock_run, _mock_remote):
        mock_run.return_value = MagicMock(returncode=0, stdout="garbage", stderr="")
        self.assertIsNone(detect_provider())


class TestDetectSelfAssignee(unittest.TestCase):
    @patch("lib.ai_workflow.run")
    def test_gh(self, mock_run):
        info = ProviderInfo(provider="gh", host="github.com", repo="o/r",
                            remote="origin", remote_url="")
        mock_run.return_value = MagicMock(returncode=0, stdout="myuser\n", stderr="")
        self.assertEqual(detect_self_assignee(info), "myuser")

    @patch("lib.ai_workflow.run")
    def test_glab(self, mock_run):
        info = ProviderInfo(provider="glab", host="gitlab.com", repo="o/r",
                            remote="origin", remote_url="")
        mock_run.return_value = MagicMock(returncode=0,
                                          stdout='{"username":"glabuser"}\n', stderr="")
        self.assertEqual(detect_self_assignee(info), "glabuser")

    @patch("lib.ai_workflow.run")
    def test_glab_invalid_json(self, mock_run):
        info = ProviderInfo(provider="glab", host="gitlab.com", repo="o/r",
                            remote="origin", remote_url="")
        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        self.assertEqual(detect_self_assignee(info), "")

    @patch("lib.ai_workflow.run")
    def test_failure_returns_empty(self, mock_run):
        info = ProviderInfo(provider="gh", host="github.com", repo="o/r",
                            remote="origin", remote_url="")
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        self.assertEqual(detect_self_assignee(info), "")


class TestRemoteDefaultBranch(unittest.TestCase):
    @patch("lib.ai_workflow.run")
    def test_found(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="origin/main\n", stderr="")
        self.assertEqual(remote_default_branch("origin"), "main")

    @patch("lib.ai_workflow.run")
    def test_no_prefix(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="develop\n", stderr="")
        self.assertEqual(remote_default_branch("origin"), "develop")

    @patch("lib.ai_workflow.run")
    def test_fallback_main(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        self.assertEqual(remote_default_branch("origin"), "main")


class TestProviderInfoDataclass(unittest.TestCase):
    def test_fields(self):
        info = ProviderInfo(provider="gh", host="h", repo="r", remote="o", remote_url="u")
        self.assertEqual(info.provider, "gh")
        self.assertEqual(info.repo, "r")


if __name__ == "__main__":
    unittest.main()

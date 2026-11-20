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


class TestParseRemoteUrlEdgeCases(unittest.TestCase):
    def test_host_only_no_path(self):
        self.assertIsNone(parse_remote_url("git@github.com:"))


class TestDetectProviderEdgeCases(unittest.TestCase):
    @patch("lib.ai_workflow.primary_remote", return_value="origin")
    @patch("lib.ai_workflow.run")
    def test_empty_remote_url(self, mock_run, _mock_remote):
        mock_run.return_value = MagicMock(returncode=0, stdout="  \n", stderr="")
        self.assertIsNone(detect_provider())


class TestSplitTitleBody(unittest.TestCase):
    def test_first_line_title_rest_body(self):
        from lib.ai_workflow import split_title_body
        self.assertEqual(split_title_body("修登录\n\n## Summary\n- a"), ("修登录", "## Summary\n- a"))

    def test_strips_fence_prefix_and_quotes(self):
        from lib.ai_workflow import split_title_body
        text = "```\n\nTitle: “修登录”\n\nbody\n```"
        self.assertEqual(split_title_body(text), ("修登录", "body"))

    def test_markdown_heading_and_inner_fences_kept(self):
        from lib.ai_workflow import split_title_body
        text = "## 加缓存\n\n```py\nx = 1\n```"
        self.assertEqual(split_title_body(text), ("加缓存", "```py\nx = 1\n```"))

    def test_empty(self):
        from lib.ai_workflow import split_title_body
        self.assertEqual(split_title_body("  \n"), ("", ""))


class TestCreateWithGeneratedText(unittest.TestCase):
    def _call(self, text, retry=None):
        from lib.ai_workflow import create_with_generated_text
        from lib.exec import RetryResult
        retry = retry or MagicMock(return_value=RetryResult(ok=True, attempts=1, last_output="https://x/1\n"))
        r = MagicMock()
        with patch("lib.ai_workflow.generate_via_lazygophers", return_value=text) as gen, \
             patch("lib.ai_workflow.retry_command", retry), \
             patch("lib.ai_workflow.reporter", return_value=r), \
             patch("builtins.print") as out:
            rc = create_with_generated_text(
                "p", system_prompt="s", build_argv=lambda t, b: ["gh", "x", "create", t, b])
        return rc, retry, r, out, gen

    def test_runs_argv_built_from_title_and_body(self):
        rc, retry, _, out, gen = self._call("标题\n\n正文")
        self.assertEqual(rc, 0)
        self.assertEqual(retry.call_args.args[0], ["gh", "x", "create", "标题", "正文"])
        self.assertEqual(retry.call_args.kwargs["max_retries"], 1)
        out.assert_called_once_with("https://x/1")
        self.assertEqual(gen.call_args.kwargs["system_prompt"], "s")

    def test_generation_failure_skips_the_command(self):
        rc, retry, _, _, _ = self._call("")
        self.assertEqual(rc, 1)
        retry.assert_not_called()

    def test_missing_title_skips_the_command(self):
        rc, retry, r, _, _ = self._call("```\n```")
        self.assertEqual(rc, 1)
        retry.assert_not_called()
        r.err.assert_called_once()

    def test_command_failure_surfaces_output_verbatim(self):
        from lib.exec import RetryResult
        retry = MagicMock(return_value=RetryResult(ok=False, attempts=1,
                                                   last_output="GraphQL: No commits between main and feat\n"))
        rc, _, r, out, _ = self._call("t\n\nb", retry=retry)
        self.assertEqual(rc, 1)
        self.assertIn("GraphQL: No commits between main and feat", r.err.call_args[0][0])
        out.assert_not_called()


if __name__ == "__main__":
    unittest.main()

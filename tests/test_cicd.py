#!/usr/bin/env python3
"""Tests for lib.cicd / bin.cicd."""
import sys
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.ai_workflow import ProviderInfo
from lib.cicd import (
    CiStatus,
    PollConfig,
    build_logs_command,
    build_run_status_command,
    build_status_command,
    build_trigger_command,
    classify_status,
    logs_cicd,
    resolve_provider,
    status_cicd,
    trigger_cicd,
    watch_cicd,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
_cicd_bin = SourceFileLoader("cicd_bin_test_mod", str(REPO_ROOT / "bin" / "cicd")).load_module()


def _gh_info() -> ProviderInfo:
    return ProviderInfo(
        provider="gh", host="github.com", repo="owner/repo",
        remote="origin", remote_url="git@github.com:owner/repo.git",
    )


def _glab_info() -> ProviderInfo:
    return ProviderInfo(
        provider="glab", host="gitlab.example.com", repo="owner/repo",
        remote="origin", remote_url="git@gitlab.example.com:owner/repo.git",
    )


class TestResolveProvider(unittest.TestCase):
    @patch("lib.cicd.detect_provider", return_value=_gh_info())
    def test_default_uses_git_remote(self, mock_detect):
        self.assertEqual(resolve_provider(), _gh_info())
        mock_detect.assert_called_once()

    def test_github_url(self):
        info = resolve_provider("https://github.com/owner/repo.git")
        self.assertEqual(info.provider, "gh")
        self.assertEqual(info.repo, "owner/repo")

    def test_gitlab_url(self):
        info = resolve_provider("https://gitlab.example.com/owner/repo.git")
        self.assertEqual(info.provider, "glab")
        self.assertEqual(info.repo, "owner/repo")


class TestBuildCommands(unittest.TestCase):
    def test_gh_status(self):
        self.assertEqual(
            build_status_command(_gh_info(), ref="feat"),
            [
                "gh", "run", "list",
                "--branch", "feat",
                "--limit", "1",
                "--json", "conclusion,databaseId,displayTitle,status,url,workflowName",
                "--repo", "owner/repo",
            ],
        )

    def test_glab_status(self):
        self.assertEqual(
            build_status_command(_glab_info(), ref="feat"),
            ["glab", "ci", "status", "--branch", "feat", "--output", "json", "--repo", "git@gitlab.example.com:owner/repo.git"],
        )

    def test_gh_run_status(self):
        self.assertEqual(
            build_run_status_command(_gh_info(), "123"),
            [
                "gh", "run", "view", "123",
                "--json", "conclusion,databaseId,displayTitle,status,url,workflowName",
                "--repo", "owner/repo",
            ],
        )

    def test_glab_run_status(self):
        self.assertEqual(
            build_run_status_command(_glab_info(), "123"),
            ["glab", "ci", "view", "123", "--repo", "git@gitlab.example.com:owner/repo.git"],
        )

    def test_gh_trigger(self):
        self.assertEqual(
            build_trigger_command(_gh_info(), workflow="ci.yml", ref="feat"),
            ["gh", "workflow", "run", "ci.yml", "--ref", "feat", "--repo", "owner/repo"],
        )

    def test_glab_trigger(self):
        self.assertEqual(
            build_trigger_command(_glab_info(), workflow="", ref="feat"),
            ["glab", "ci", "run", "--branch", "feat", "--repo", "git@gitlab.example.com:owner/repo.git"],
        )

    def test_gh_logs_failed_with_job(self):
        self.assertEqual(
            build_logs_command(_gh_info(), "123", failed=True, job="456"),
            ["gh", "run", "view", "123", "--log-failed", "--repo", "owner/repo", "--job", "456"],
        )

    def test_glab_logs(self):
        self.assertEqual(
            build_logs_command(_glab_info(), "456"),
            ["glab", "ci", "trace", "456", "--repo", "git@gitlab.example.com:owner/repo.git"],
        )


class TestClassifyStatus(unittest.TestCase):
    def test_gh_pending(self):
        self.assertEqual(
            classify_status(_gh_info(), '[{"status":"in_progress","conclusion":""}]', "", 0),
            "running",
        )

    def test_gh_pass(self):
        self.assertEqual(classify_status(_gh_info(), '[{"status":"completed","conclusion":"success"}]', "", 0), "pass")

    def test_gh_fail(self):
        self.assertEqual(classify_status(_gh_info(), '[{"status":"completed","conclusion":"failure"}]', "", 0), "fail")

    def test_gh_no_checks_is_done(self):
        self.assertEqual(classify_status(_gh_info(), "[]", "", 0), "no-checks")

    def test_glab_running(self):
        self.assertEqual(classify_status(_glab_info(), '{"status":"running"}', "", 0), "running")

    def test_glab_fail(self):
        self.assertEqual(classify_status(_glab_info(), '{"status":"failed"}', "", 1), "fail")

    def test_glab_skipped_is_done(self):
        self.assertEqual(classify_status(_glab_info(), '{"status":"skipped"}', "", 0), "pass")


class TestCicdActions(unittest.TestCase):
    @patch("lib.cicd.reporter")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    @patch("lib.cicd.current_branch", return_value="feat")
    @patch("lib.cicd.run")
    def test_trigger_runs_command(self, mock_run, _mock_branch, _mock_provider, mock_reporter):
        fake = MagicMock()
        mock_reporter.return_value = fake
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        rc = trigger_cicd("ci.yml")
        self.assertEqual(rc, 0)
        mock_run.assert_called_once()
        self.assertIn("workflow", mock_run.call_args.args[0])
        fake.ok.assert_called_once()

    @patch("lib.cicd.resolve_provider", return_value=_glab_info())
    @patch("lib.cicd.run")
    def test_glab_trigger_treats_first_arg_as_ref(self, mock_run, _mock_provider):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        rc = trigger_cicd("feat")
        self.assertEqual(rc, 0)
        self.assertEqual(mock_run.call_args.args[0][4], "feat")

    @patch("lib.cicd.reporter")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_gh_trigger_requires_workflow(self, _mock_provider, mock_reporter):
        fake = MagicMock()
        mock_reporter.return_value = fake
        rc = trigger_cicd("", "feat")
        self.assertEqual(rc, 2)
        fake.err.assert_called_once()

    @patch("lib.cicd.reporter")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    @patch("lib.cicd.check_once", return_value=CiStatus("pass", "ok", ["cmd"]))
    def test_status_checks_branch_once(self, mock_check, _mock_provider, mock_reporter):
        mock_reporter.return_value = MagicMock()
        rc = status_cicd("feat")
        self.assertEqual(rc, 0)
        mock_check.assert_called_once_with(_gh_info(), ref="feat")

    @patch("lib.cicd.reporter")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    @patch("lib.cicd.run")
    def test_logs_outputs_command_output(self, mock_run, _mock_provider, mock_reporter):
        fake = MagicMock()
        mock_reporter.return_value = fake
        mock_run.return_value = MagicMock(returncode=0, stdout="log", stderr="")
        rc = logs_cicd("123", failed=True)
        self.assertEqual(rc, 0)
        fake.output.assert_called_once()


class TestWatchCicd(unittest.TestCase):
    @patch("lib.cicd.notify")
    @patch("lib.cicd.random.uniform", return_value=5.0)
    @patch("lib.cicd.time.sleep")
    @patch("lib.cicd.reporter")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    @patch("lib.cicd.current_branch", return_value="feat")
    @patch("lib.cicd.check_once")
    def test_waits_until_done(
        self,
        mock_check,
        _mock_branch,
        _mock_provider,
        mock_reporter,
        mock_sleep,
        _mock_rand,
        mock_notify,
    ):
        mock_check.side_effect = [
            CiStatus("running", "still running", ["gh", "run", "list", "--branch", "feat"]),
            CiStatus("pass", "all good", ["gh", "run", "list", "--branch", "feat"]),
        ]
        fake = MagicMock()
        mock_reporter.return_value = fake
        rc = watch_cicd("feat", config=PollConfig(min_interval=5.0, max_interval=5.0))
        self.assertEqual(rc, 0)
        mock_sleep.assert_called_once_with(5.0)
        fake.info.assert_not_called()
        fake.rule.assert_called_once()
        fake.output.assert_called_once()
        mock_notify.assert_called_once_with("CI/CD pass")

    @patch("lib.cicd.notify")
    @patch("lib.cicd.random.uniform", return_value=5.0)
    @patch("lib.cicd.time.sleep")
    @patch("lib.cicd.reporter")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    @patch("lib.cicd.check_run_once")
    def test_watch_target_uses_run_status(self, mock_check, _mock_provider, mock_reporter, mock_sleep, _mock_rand, _mock_notify):
        mock_check.side_effect = [
            CiStatus("running", "still running", ["gh", "run", "view", "123"]),
            CiStatus("pass", "all good", ["gh", "run", "view", "123"]),
        ]
        mock_reporter.return_value = MagicMock()
        rc = watch_cicd(target="123", config=PollConfig(min_interval=5.0, max_interval=5.0))
        self.assertEqual(rc, 0)
        mock_sleep.assert_called_once_with(5.0)
        self.assertEqual(mock_check.call_args.args[1], "123")

    @patch("lib.cicd.reporter")
    def test_bad_interval_rejected(self, mock_reporter):
        fake = MagicMock()
        mock_reporter.return_value = fake
        rc = watch_cicd("feat", config=PollConfig(min_interval=8.0, max_interval=5.0))
        self.assertEqual(rc, 2)
        fake.err.assert_called_once()


class TestCicdCli(unittest.TestCase):
    @patch.object(_cicd_bin, "watch_cicd", return_value=0)
    def test_call_aliases_watch(self, mock_watch):
        cli = _cicd_bin.CicdCli()
        rc = cli("feat", min_interval=1.0, max_interval=2.0, project="owner/repo")
        self.assertEqual(rc, 0)
        self.assertEqual(mock_watch.call_args.args[0], "feat")
        self.assertEqual(mock_watch.call_args.kwargs["project"], "owner/repo")
        config = mock_watch.call_args.kwargs["config"]
        self.assertEqual(config.min_interval, 1.0)

    @patch.object(_cicd_bin, "trigger_cicd", return_value=0)
    def test_trigger_subcommand(self, mock_trigger):
        cli = _cicd_bin.CicdCli()
        rc = cli.trigger("ci.yml", "feat", project="owner/repo")
        self.assertEqual(rc, 0)
        mock_trigger.assert_called_once_with(workflow="ci.yml", ref="feat", project="owner/repo")

    @patch.object(_cicd_bin, "status_cicd", return_value=0)
    def test_status_subcommand(self, mock_status):
        cli = _cicd_bin.CicdCli()
        rc = cli.status("feat", project="owner/repo")
        self.assertEqual(rc, 0)
        mock_status.assert_called_once_with("feat", project="owner/repo")

    @patch.object(_cicd_bin, "logs_cicd", return_value=0)
    def test_logs_subcommand(self, mock_logs):
        cli = _cicd_bin.CicdCli()
        rc = cli.logs("123", project="owner/repo", failed=True, job="456")
        self.assertEqual(rc, 0)
        mock_logs.assert_called_once_with("123", project="owner/repo", failed=True, job="456")


if __name__ == "__main__":
    unittest.main()

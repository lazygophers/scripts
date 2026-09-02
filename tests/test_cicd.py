#!/usr/bin/env python3
"""Tests for lib.cicd / bin.cicd."""
import sys
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.cicd import CiStatus, PollConfig, build_status_command, classify_status, watch_cicd
from lib.ai_workflow import ProviderInfo

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


class TestBuildStatusCommand(unittest.TestCase):
    def test_gh(self):
        self.assertEqual(
            build_status_command(_gh_info(), ref="feat"),
            [
                "gh", "run", "list",
                "--branch", "feat",
                "--limit", "1",
                "--json", "conclusion,databaseId,displayTitle,status,url,workflowName",
            ],
        )

    def test_glab(self):
        self.assertEqual(
            build_status_command(_glab_info(), ref="feat"),
            ["glab", "ci", "status", "--branch", "feat", "--output", "json"],
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


class TestWatchCicd(unittest.TestCase):
    @patch("lib.cicd.random.uniform", return_value=5.0)
    @patch("lib.cicd.time.sleep")
    @patch("lib.cicd.reporter")
    @patch("lib.cicd.detect_provider", return_value=_gh_info())
    @patch("lib.cicd.current_branch", return_value="feat")
    @patch("lib.cicd.check_once")
    def test_waits_until_done(self, mock_check, _mock_branch, _mock_provider, mock_reporter, mock_sleep, _mock_rand):
        mock_check.side_effect = [
            CiStatus("running", "still running", ["gh", "run", "list", "--branch", "feat"]),
            CiStatus("pass", "all good", ["gh", "run", "list", "--branch", "feat"]),
        ]
        fake = MagicMock()
        mock_reporter.return_value = fake
        rc = watch_cicd("feat", config=PollConfig(min_interval=5.0, max_interval=5.0))
        self.assertEqual(rc, 0)
        mock_sleep.assert_called_once_with(5.0)
        fake.rule.assert_called_once()
        fake.output.assert_called_once()

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
        rc = cli("feat", min_interval=1.0, max_interval=2.0, once=True)
        self.assertEqual(rc, 0)
        self.assertEqual(mock_watch.call_args.args[0], "feat")
        config = mock_watch.call_args.kwargs["config"]
        self.assertEqual(config.min_interval, 1.0)
        self.assertTrue(config.once)


if __name__ == "__main__":
    unittest.main()

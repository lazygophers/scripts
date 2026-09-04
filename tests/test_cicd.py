#!/usr/bin/env python3
"""Tests for lib.cicd / bin.cicd."""
import subprocess
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
    _extract_glab_status,
    _run_status,
    _validate_config,
    build_logs_command,
    build_run_status_command,
    build_status_command,
    build_trigger_command,
    check_once,
    check_run_once,
    classify_status,
    logs_cicd,
    resolve_provider,
    status_cicd,
    trigger_cicd,
    watch_cicd,
)
from lib.exec import CommandTimeout

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

    def test_bare_github_path(self):
        info = resolve_provider("github.com/owner/repo")
        self.assertEqual((info.provider, info.host, info.repo), ("gh", "github.com", "owner/repo"))

    def test_github_prefix_without_repo_path(self):
        """`github.com/` 解析不出 repo，但仍应认成 GitHub 而不是 GitLab。"""
        info = resolve_provider("github.com/")
        self.assertEqual((info.provider, info.host, info.repo), ("gh", "github.com", ""))

    def test_repo_less_provider_adds_no_repo_flag(self):
        info = ProviderInfo(provider="gh", host="github.com", repo="", remote="", remote_url="")
        self.assertEqual(build_status_command(info, ref="feat")[-1],
                         "conclusion,databaseId,displayTitle,status,url,workflowName")

    def test_glab_logs_with_job(self):
        self.assertEqual(
            build_logs_command(_glab_info(), "456", job="789")[-2:],
            ["--job", "789"],
        )


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


class TestGlabClassifyFallbacks(unittest.TestCase):
    """glab 输出里没有可解析的 status 字段时，只能靠文本关键字和退出码判断。"""

    def classify(self, stdout: str, stderr: str = "", code: int = 0) -> str:
        return classify_status(_glab_info(), stdout, stderr, code)

    def test_status_from_detailed_status_text(self):
        self.assertEqual(_extract_glab_status('{"detailed_status":{"text":"Passed"}}'), "passed")

    def test_status_from_broken_json_is_empty(self):
        self.assertEqual(_extract_glab_status("not json"), "")

    def test_status_from_json_list_is_empty(self):
        self.assertEqual(_extract_glab_status("[1,2]"), "")

    def test_success_exit_with_failure_text(self):
        self.assertEqual(self.classify("pipeline failed for branch"), "fail")

    def test_success_exit_with_pending_text(self):
        self.assertEqual(self.classify("job is running"), "running")

    def test_success_exit_without_output_is_no_checks(self):
        self.assertEqual(self.classify(""), "no-checks")

    def test_success_exit_with_unknown_text_is_pass(self):
        self.assertEqual(self.classify("everything nominal"), "pass")

    def test_failure_exit_with_pending_text(self):
        self.assertEqual(self.classify("", "pipeline pending", 1), "running")

    def test_failure_exit_with_failure_text(self):
        self.assertEqual(self.classify("", "pipeline canceled", 1), "fail")

    def test_failure_exit_without_markers_is_error(self):
        self.assertEqual(self.classify("", "permission denied", 1), "error")

    def test_gh_broken_json_with_failure_exit_is_error(self):
        self.assertEqual(classify_status(_gh_info(), "<html>", "", 1), "error")

    def test_gh_broken_json_with_success_exit_is_pass(self):
        self.assertEqual(classify_status(_gh_info(), "<html>", "", 0), "pass")

    def test_gh_non_dict_row_is_error(self):
        self.assertEqual(classify_status(_gh_info(), '["nope"]', "", 0), "error")

    def test_gh_unknown_conclusion_still_running(self):
        self.assertEqual(classify_status(_gh_info(), '[{"status":"x","conclusion":"y"}]', "", 0), "running")

    def test_gh_no_workflow_runs_text(self):
        self.assertEqual(classify_status(_gh_info(), "", "no workflow runs found", 1), "no-checks")


class TestRunStatus(unittest.TestCase):
    @patch("lib.cicd.run")
    def test_timeout_becomes_error_status(self, mock_run):
        mock_run.side_effect = CommandTimeout("超时了")
        status = _run_status(["gh", "run", "list"], _gh_info())
        self.assertEqual((status.state, status.returncode), ("error", 124))

    @patch("lib.cicd.run")
    def test_detail_falls_back_to_command_line(self, mock_run):
        mock_run.return_value = MagicMock(returncode=3, stdout="", stderr="")
        status = _run_status(["gh", "run", "list"], _gh_info())
        self.assertIn("exit 3", status.detail)

    @patch("lib.cicd.run")
    def test_check_once_builds_branch_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        status = check_once(_gh_info(), ref="feat")
        self.assertEqual(status.state, "no-checks")
        self.assertIn("--branch", mock_run.call_args.args[0])

    @patch("lib.cicd.run")
    def test_check_run_once_builds_view_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")
        check_run_once(_gh_info(), "123")
        self.assertIn("view", mock_run.call_args.args[0])


class TestValidateConfig(unittest.TestCase):
    def test_negative_interval(self):
        self.assertEqual(_validate_config(PollConfig(min_interval=-1)), "间隔不能小于 0")

    def test_min_over_max(self):
        self.assertEqual(_validate_config(PollConfig(min_interval=9, max_interval=1)), "最小间隔不能大于最大间隔")

    def test_non_positive_timeout(self):
        self.assertEqual(_validate_config(PollConfig(timeout=0)), "timeout 必须大于 0")

    def test_valid_config(self):
        self.assertIsNone(_validate_config(PollConfig(timeout=10)))


class TestActionGuards(unittest.TestCase):
    """没有 provider、不在普通分支、命令失败这三类早退路径。"""

    def setUp(self):
        self.fake = MagicMock()
        patcher = patch("lib.cicd.reporter", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("lib.cicd.resolve_provider", return_value=None)
    def test_status_without_provider(self, _mock):
        self.assertEqual(status_cicd("feat"), 2)
        self.fake.err.assert_called_once()

    @patch("lib.cicd.resolve_provider", return_value=None)
    def test_trigger_without_provider(self, _mock):
        self.assertEqual(trigger_cicd("ci.yml", "feat"), 2)

    @patch("lib.cicd.resolve_provider", return_value=None)
    def test_logs_without_provider(self, _mock):
        self.assertEqual(logs_cicd("123"), 2)

    @patch("lib.cicd.resolve_provider", return_value=None)
    def test_watch_without_provider(self, _mock):
        self.assertEqual(watch_cicd("feat"), 2)

    @patch("lib.cicd.current_branch", return_value="detached")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_status_on_detached_head(self, _mock_provider, _mock_branch):
        self.assertEqual(status_cicd(), 2)

    @patch("lib.cicd.current_branch", return_value="detached")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_trigger_on_detached_head(self, _mock_provider, _mock_branch):
        self.assertEqual(trigger_cicd("ci.yml"), 2)

    @patch("lib.cicd.current_branch", return_value="detached")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_watch_on_detached_head(self, _mock_provider, _mock_branch):
        self.assertEqual(watch_cicd(), 2)

    @patch("lib.cicd.run")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_trigger_reports_command_failure(self, _mock_provider, mock_run):
        mock_run.return_value = MagicMock(returncode=7, stdout="", stderr="boom")
        self.assertEqual(trigger_cicd("ci.yml", "feat"), 7)
        self.fake.err.assert_called_once_with("CI/CD 触发失败")
        self.fake.output.assert_called_once()

    @patch("lib.cicd.run")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_trigger_success_without_output(self, _mock_provider, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        self.assertEqual(trigger_cicd("ci.yml", "feat"), 0)
        self.fake.output.assert_not_called()

    @patch("lib.cicd.run")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_logs_silent_when_no_output(self, _mock_provider, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        self.assertEqual(logs_cicd("123"), 0)
        self.fake.output.assert_not_called()

    @patch("lib.cicd.check_once", return_value=CiStatus("fail", "boom", ["cmd"]))
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_status_failure_returns_one(self, _mock_provider, _mock_check):
        self.assertEqual(status_cicd("feat"), 1)


class TestWatchEdges(unittest.TestCase):
    def setUp(self):
        self.fake = MagicMock()
        for target, kwargs in (("lib.cicd.reporter", {"return_value": self.fake}),
                               ("lib.cicd.notify", {}),
                               ("lib.cicd.time.sleep", {}),
                               ("lib.cicd.random.uniform", {"return_value": 0.0})):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    @patch("lib.cicd.check_once")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_verbose_prints_each_attempt(self, _mock_provider, mock_check):
        mock_check.side_effect = [CiStatus("running", "…", ["cmd"]), CiStatus("pass", "ok", ["cmd"])]
        rc = watch_cicd("feat", config=PollConfig(min_interval=0, max_interval=0, verbose=True))
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake.info.call_count, 2)

    @patch("lib.cicd.check_once")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_error_state_is_reported_but_keeps_polling(self, _mock_provider, mock_check):
        mock_check.side_effect = [CiStatus("error", "api down", ["cmd"]), CiStatus("fail", "bad", ["cmd"])]
        rc = watch_cicd("feat", config=PollConfig(min_interval=0, max_interval=0))
        self.assertEqual(rc, 1)
        self.fake.err.assert_called_once_with("api down")

    @patch("lib.cicd.check_once", return_value=CiStatus("running", "…", ["cmd"]))
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_once_stops_after_first_poll(self, _mock_provider, mock_check):
        rc = watch_cicd("feat", config=PollConfig(min_interval=0, max_interval=0, once=True))
        self.assertEqual(rc, 1)
        mock_check.assert_called_once()

    @patch("lib.cicd.check_once", return_value=CiStatus("running", "…", ["cmd"]))
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_timeout_returns_one(self, _mock_provider, _mock_check):
        rc = watch_cicd("feat", config=PollConfig(min_interval=0, max_interval=0, timeout=0.001))
        self.assertEqual(rc, 1)
        self.fake.rule.assert_called_once()

    @patch("lib.cicd.check_once", side_effect=KeyboardInterrupt)
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_interrupt_before_first_result(self, _mock_provider, _mock_check):
        rc = watch_cicd("feat", config=PollConfig(min_interval=0, max_interval=0))
        self.assertEqual(rc, 130)
        self.fake.warn.assert_called_once()

    @patch("lib.cicd.check_once")
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_interrupt_after_a_result_prints_last_status(self, _mock_provider, mock_check):
        mock_check.side_effect = [CiStatus("running", "…", ["cmd"]), KeyboardInterrupt]
        rc = watch_cicd("feat", config=PollConfig(min_interval=0, max_interval=0))
        self.assertEqual(rc, 130)
        self.fake.rule.assert_called_once()


class TestPrintFinal(unittest.TestCase):
    """收尾输出走真实 Reporter，验证状态、耗时、命令都打出来了。"""

    @patch("lib.cicd.notify")
    @patch("lib.cicd.check_once", return_value=CiStatus("fail", "boom", ["gh", "run", "list"]))
    @patch("lib.cicd.resolve_provider", return_value=_gh_info())
    def test_summary_contains_state_and_command(self, _mock_provider, _mock_check, _mock_notify):
        import io

        from rich.console import Console

        from lib.ui import Reporter

        buf = io.StringIO()
        real = Reporter()
        real.console = Console(file=buf, width=200, force_terminal=False)
        with patch("lib.cicd.reporter", return_value=real):
            rc = watch_cicd("feat", config=PollConfig(min_interval=0, max_interval=0, once=True))
        self.assertEqual(rc, 1)
        text = buf.getvalue()
        self.assertIn("fail", text)
        self.assertIn("gh run list", text)


class TestCicdCli(unittest.TestCase):
    def test_bare_command_shows_skills(self):
        proc = subprocess.run([str(REPO_ROOT / "bin" / "cicd")], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stderr + proc.stdout
        self.assertIn("# cicd skills", out)
        self.assertIn("cicd run", out)

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

    @patch.object(_cicd_bin, "status_cicd", return_value=0)
    def test_now_aliases_status(self, mock_status):
        cli = _cicd_bin.CicdCli()
        rc = cli.now("feat", project="owner/repo")
        self.assertEqual(rc, 0)
        mock_status.assert_called_once_with("feat", project="owner/repo")

    @patch.object(_cicd_bin, "trigger_cicd", return_value=0)
    def test_run_aliases_trigger(self, mock_trigger):
        cli = _cicd_bin.CicdCli()
        rc = cli.run("ci.yml", "feat", project="owner/repo")
        self.assertEqual(rc, 0)
        mock_trigger.assert_called_once_with(workflow="ci.yml", ref="feat", project="owner/repo")

    @patch.object(_cicd_bin, "watch_cicd", return_value=0)
    def test_id_aliases_watch_target(self, mock_watch):
        cli = _cicd_bin.CicdCli()
        rc = cli.id("123", project="owner/repo", once=True)
        self.assertEqual(rc, 0)
        self.assertEqual(mock_watch.call_args.kwargs["target"], "123")
        self.assertTrue(mock_watch.call_args.kwargs["config"].once)

    @patch.object(_cicd_bin, "logs_cicd", return_value=0)
    def test_log_aliases_logs(self, mock_logs):
        cli = _cicd_bin.CicdCli()
        rc = cli.log("123", project="owner/repo")
        self.assertEqual(rc, 0)
        mock_logs.assert_called_once_with("123", project="owner/repo", failed=False, job="")

    @patch.object(_cicd_bin, "logs_cicd", return_value=0)
    def test_fail_aliases_failed_logs(self, mock_logs):
        cli = _cicd_bin.CicdCli()
        rc = cli.fail("123", project="owner/repo", job="456")
        self.assertEqual(rc, 0)
        mock_logs.assert_called_once_with("123", project="owner/repo", failed=True, job="456")

    @patch.object(_cicd_bin, "logs_cicd", return_value=0)
    def test_logs_subcommand(self, mock_logs):
        cli = _cicd_bin.CicdCli()
        rc = cli.logs("123", project="owner/repo", failed=True, job="456")
        self.assertEqual(rc, 0)
        mock_logs.assert_called_once_with("123", project="owner/repo", failed=True, job="456")


if __name__ == "__main__":
    unittest.main()

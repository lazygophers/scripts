#!/usr/bin/env python3
"""Tests for lib.git 的核心分支操作（check_bit_clean / update_branch / 渲染降级）。"""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import lib.git as git_mod
from lib.git import (
    GitError,
    _render_branch_table,
    _report,
    _rollback_to_branch,
    _run_git_retry,
    _switch_to_branch,
    check_bit_clean,
    ensure_tool_exists,
    fetch_and_check_branch,
    get_current_branch,
    remote_branch_exists,
    update_branch,
)


def _proc(returncode=0, stdout="", stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def _retry(ok=True, output=""):
    return MagicMock(ok=ok, last_output=output)


class TestCheckBitClean(unittest.TestCase):
    @patch("lib.git.run")
    def test_clean_passes(self, mock_run):
        mock_run.return_value = _proc(0, "")
        check_bit_clean()

    @patch("lib.git.run")
    def test_nonzero_returncode_raises(self, mock_run):
        mock_run.return_value = _proc(128, "", "not a git repository")
        with self.assertRaises(GitError) as cm:
            check_bit_clean()
        self.assertIn("not a git repository", str(cm.exception))

    @patch("lib.git.run")
    def test_nonzero_without_output_uses_default_msg(self, mock_run):
        mock_run.return_value = _proc(1, "", "")
        with self.assertRaises(GitError) as cm:
            check_bit_clean()
        self.assertIn("git 仓库", str(cm.exception))

    @patch("lib.git.run")
    def test_dirty_worktree_raises(self, mock_run):
        mock_run.return_value = _proc(0, " M lib/git.py\n")
        with self.assertRaises(GitError) as cm:
            check_bit_clean()
        self.assertIn("未提交", str(cm.exception))

    @patch("lib.git.run")
    def test_untracked_raises(self, mock_run):
        mock_run.return_value = _proc(0, "?? new.txt\n")
        with self.assertRaises(GitError):
            check_bit_clean()

    @patch("lib.git.run")
    def test_custom_bit_cmd(self, mock_run):
        mock_run.return_value = _proc(0, "")
        check_bit_clean(bit_cmd="bit")
        self.assertEqual(mock_run.call_args[0][0][0], "bit")


class TestGetCurrentBranch(unittest.TestCase):
    @patch("lib.git.run")
    def test_strips(self, mock_run):
        mock_run.return_value = _proc(0, "feature/x\n")
        self.assertEqual(get_current_branch(), "feature/x")

    @patch("lib.git.run")
    def test_detached_head_empty(self, mock_run):
        mock_run.return_value = _proc(0, "")
        self.assertEqual(get_current_branch(), "")


class TestSwitchToBranch(unittest.TestCase):
    @patch("lib.git.run")
    def test_plain_checkout_succeeds(self, mock_run):
        mock_run.return_value = _proc(0)
        _switch_to_branch("dev", "git", "origin", "main")
        self.assertEqual(mock_run.call_count, 1)

    @patch("lib.git.run")
    def test_falls_back_to_track(self, mock_run):
        mock_run.side_effect = [_proc(1), _proc(0)]
        _switch_to_branch("dev", "git", "origin", "main")
        self.assertEqual(mock_run.call_args[0][0][:3], ["git", "checkout", "-b"])

    @patch("lib.git.run")
    def test_both_fail_raises(self, mock_run):
        mock_run.side_effect = [_proc(1), _proc(1)]
        with self.assertRaises(GitError) as cm:
            _switch_to_branch("dev", "git", "origin", "main")
        self.assertIn("dev", str(cm.exception))


class TestReportHelper(unittest.TestCase):
    def test_none_reporter_noop(self):
        _report(None, "info", "x")

    def test_missing_method_noop(self):
        _report(object(), "no_such_method", "x")

    def test_calls_method(self):
        r = MagicMock()
        _report(r, "warn", "careful")
        r.warn.assert_called_once_with("careful")

    def test_swallows_reporter_exception(self):
        r = MagicMock()
        r.warn.side_effect = RuntimeError("broken console")
        _report(r, "warn", "careful")


class TestRollback(unittest.TestCase):
    @patch("lib.git.run")
    def test_empty_branch_noop(self, mock_run):
        _rollback_to_branch("", "git")
        mock_run.assert_not_called()

    @patch("lib.git.run")
    def test_checks_out_original(self, mock_run):
        _rollback_to_branch("main", "git")
        self.assertEqual(mock_run.call_args[0][0], ["git", "checkout", "main"])


class TestRunGitRetry(unittest.TestCase):
    @patch("lib.git.retry_command")
    def test_success_silent_when_no_output(self, mock_retry):
        mock_retry.return_value = _retry(True, "  \n")
        r = MagicMock()
        _run_git_retry(["git", "push"], bit_cmd="git", original_branch="main",
                       r=r, error_msg="推送失败", title="push")
        r.output.assert_not_called()

    @patch("lib.git.retry_command")
    def test_success_prints_output(self, mock_retry):
        mock_retry.return_value = _retry(True, "Everything up-to-date")
        r = MagicMock()
        _run_git_retry(["git", "push"], bit_cmd="git", original_branch="main",
                       r=r, error_msg="推送失败", title="push")
        r.output.assert_called_once_with("Everything up-to-date")

    @patch("lib.git.run")
    @patch("lib.git.retry_command")
    def test_failure_rolls_back_and_raises(self, mock_retry, mock_run):
        mock_retry.return_value = _retry(False, "rejected")
        r = MagicMock()
        with self.assertRaises(GitError) as cm:
            _run_git_retry(["git", "push"], bit_cmd="git", original_branch="feat",
                           r=r, error_msg="推送失败", title="push")
        self.assertIn("rejected", str(cm.exception))
        self.assertEqual(mock_run.call_args[0][0], ["git", "checkout", "feat"])
        r.cmd_result.assert_called_once()


class TestUpdateBranch(unittest.TestCase):
    @patch("lib.git.check_bit_clean")
    @patch("lib.git.retry_command")
    @patch("lib.git.run")
    def test_pull_then_push(self, mock_run, mock_retry, mock_clean):
        # branch --show-current → 已在目标分支；ls-remote → 存在
        mock_run.side_effect = [_proc(0, "dev\n"), _proc(0)]
        mock_retry.return_value = _retry(True, "")
        update_branch("dev")
        cmds = [c.args[0] for c in mock_retry.call_args_list]
        self.assertEqual(cmds[0], ["git", "-c", "merge.autoEdit=false", "pull", "origin", "dev"])
        self.assertEqual(cmds[1], ["git", "push", "origin", "dev"])
        mock_clean.assert_called_once()

    @patch("lib.git.check_bit_clean")
    @patch("lib.git.retry_command")
    @patch("lib.git._switch_to_branch")
    @patch("lib.git.run")
    def test_switches_when_on_other_branch(self, mock_run, mock_switch, mock_retry, mock_clean):
        mock_run.side_effect = [_proc(0, "main\n"), _proc(0)]
        mock_retry.return_value = _retry(True, "")
        update_branch("dev")
        mock_switch.assert_called_once_with("dev", "git", "origin", "main")

    @patch("lib.git.check_bit_clean")
    @patch("lib.git.retry_command")
    @patch("lib.git.run")
    def test_skips_clean_check(self, mock_run, mock_retry, mock_clean):
        mock_run.side_effect = [_proc(0, "dev\n"), _proc(0)]
        mock_retry.return_value = _retry(True, "")
        update_branch("dev", check_after_pull=False)
        mock_clean.assert_not_called()

    @patch("lib.git.check_bit_clean")
    @patch("lib.git.retry_command")
    @patch("lib.git.run")
    def test_missing_remote_branch_pushes_upstream(self, mock_run, mock_retry, mock_clean):
        mock_run.side_effect = [_proc(0, "dev\n"), _proc(2)]
        mock_retry.return_value = _retry(True, "")
        r = MagicMock()
        update_branch("dev", r=r)
        self.assertEqual(mock_retry.call_count, 1)
        self.assertEqual(mock_retry.call_args.args[0], ["git", "push", "-u", "origin", "dev"])
        r.warn.assert_called_once()
        mock_clean.assert_called_once()

    @patch("lib.git.check_bit_clean")
    @patch("lib.git.retry_command")
    @patch("lib.git.run")
    def test_missing_remote_branch_no_clean_check(self, mock_run, mock_retry, mock_clean):
        mock_run.side_effect = [_proc(0, "dev\n"), _proc(2)]
        mock_retry.return_value = _retry(True, "")
        update_branch("dev", check_after_pull=False)
        mock_clean.assert_not_called()

    @patch("lib.git.check_bit_clean")
    @patch("lib.git.retry_command")
    @patch("lib.git.run")
    def test_pull_failure_raises(self, mock_run, mock_retry, mock_clean):
        mock_run.side_effect = [_proc(0, "dev\n"), _proc(0), _proc(0)]
        mock_retry.return_value = _retry(False, "conflict")
        with self.assertRaises(GitError) as cm:
            update_branch("dev")
        self.assertIn("拉取或合并失败", str(cm.exception))


class TestToolAndRemoteHelpers(unittest.TestCase):
    def test_ensure_tool_exists_ok(self):
        with patch("shutil.which", return_value="/usr/bin/git"):
            ensure_tool_exists("git")

    def test_ensure_tool_exists_missing_raises(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaises(GitError) as cm:
                ensure_tool_exists("nope")
        self.assertIn("nope", str(cm.exception))

    @patch("lib.git.run")
    def test_remote_branch_exists_true(self, mock_run):
        mock_run.return_value = _proc(0)
        self.assertTrue(remote_branch_exists("dev"))

    @patch("lib.git.run")
    def test_remote_branch_exists_false(self, mock_run):
        mock_run.return_value = _proc(2)
        self.assertFalse(remote_branch_exists("dev"))

    @patch("lib.git.remote_branch_exists", return_value=True)
    @patch("lib.git.run")
    def test_fetch_and_check_branch(self, mock_run, mock_exists):
        self.assertTrue(fetch_and_check_branch("dev", cwd="/repo"))
        self.assertEqual(mock_run.call_args[0][0], ["git", "fetch", "origin"])
        mock_exists.assert_called_once_with("dev", remote="origin", cwd="/repo")


class TestFetchAllWithoutProgress(unittest.TestCase):
    """progress() 返回 None 时走逐行降级路径。"""

    @patch("lib.git.progress", return_value=None)
    @patch("lib.git.retry_command")
    @patch("lib.git._list_top_repos")
    def test_success_path(self, mock_list, mock_retry, mock_prog):
        mock_list.return_value = [Path("/t/r1")]
        mock_retry.return_value = _retry(True, "")
        self.assertEqual(git_mod.fetch_all(), 0)

    @patch("lib.git.progress", return_value=None)
    @patch("lib.git.retry_command")
    @patch("lib.git._list_top_repos")
    def test_failure_path(self, mock_list, mock_retry, mock_prog):
        mock_list.return_value = [Path("/t/r1")]
        mock_retry.return_value = _retry(False, "net error")
        self.assertEqual(git_mod.fetch_all(), 1)


class _PlainReporter:
    """console=None 的 Reporter 替身，触发纯文本降级分支。"""

    console = None

    def __init__(self):
        self.lines: list[str] = []
        self.rules: list[str] = []
        self.warns: list[str] = []

    def rule(self, title, **kwargs):
        self.rules.append(title)

    def warn(self, msg):
        self.warns.append(msg)

    def _print(self, rich_text, plain_text):
        self.lines.append(plain_text)


def _branch(name, *, current=False, sha="abc1234", date="2026-08-30",
            upstream="", track=""):
    return {"name": name, "current": current, "sha": sha, "date": date,
            "upstream": upstream, "track": track}


class TestRenderBranchTablePlain(unittest.TestCase):
    def test_current_marker_and_upstream(self):
        r = _PlainReporter()
        rows = [("repoA", _branch("main", current=True, upstream="origin/main", track="[ahead 1]"))]
        _render_branch_table(r, rows, mark_duplicates=False)
        text = "\n".join(r.lines)
        self.assertIn("* main", text)
        self.assertIn("origin/main", text)
        self.assertIn("ahead 1", text)
        self.assertEqual(r.rules, ["repoA"])

    def test_track_without_upstream(self):
        r = _PlainReporter()
        rows = [("repoA", _branch("dev", track="[gone]"))]
        _render_branch_table(r, rows, mark_duplicates=False)
        self.assertIn("[[gone]]", "\n".join(r.lines))

    def test_no_sha_omits_meta(self):
        r = _PlainReporter()
        rows = [("", _branch("dev", sha=""))]
        _render_branch_table(r, rows, mark_duplicates=False)
        self.assertEqual(r.rules, ["分支"])
        self.assertNotIn("2026", "\n".join(r.lines))

    def test_duplicate_marker_and_warning(self):
        r = _PlainReporter()
        rows = [("repoA", _branch("dev")), ("repoB", _branch("dev"))]
        _render_branch_table(r, rows, mark_duplicates=True)
        self.assertIn("⟱", "\n".join(r.lines))
        self.assertEqual(len(r.warns), 1)


class TestRenderBranchTableRich(unittest.TestCase):
    def _reporter(self):
        buf = io.StringIO()
        from lib.ui import Reporter
        return Reporter(file=buf), buf

    def test_rich_table_marks_duplicates(self):
        r, buf = self._reporter()
        rows = [
            ("repoA", _branch("dev", current=True, upstream="origin/dev", track="[behind 2]")),
            ("repoB", _branch("dev")),
        ]
        _render_branch_table(r, rows, mark_duplicates=True)
        out = buf.getvalue()
        self.assertIn("repoA", out)
        self.assertIn("dev", out)
        self.assertIn("⟱", out)
        self.assertIn("behind 2", out)

    def test_rich_table_no_upstream_dash(self):
        r, buf = self._reporter()
        _render_branch_table(r, [("repoA", _branch("solo"))], mark_duplicates=False)
        self.assertIn("—", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for lib.issue_wf."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import urllib.error
from unittest.mock import MagicMock

from lib.ai_workflow import ProviderInfo
from lib.exec import RetryResult
from lib.issue_wf import _build_prompt, run_issue

_INFO = ProviderInfo(provider="gh", host="github.com", repo="o/r",
                     remote="origin", remote_url="git@github.com:o/r.git")
_GLAB = ProviderInfo(provider="glab", host="gitlab.com", repo="g/p",
                     remote="origin", remote_url="")
_ENV = {"LAZYGOPHERS_SCRIPTS_BASE_URL": "http://api", "LAZYGOPHERS_SCRIPTS_TOKEN": "t"}
_OK = RetryResult(ok=True, attempts=1, last_output="https://github.com/o/r/issues/1\n")


def _git(*_a, **_kw):
    return MagicMock(returncode=0, stdout="abc fix: y\n", stderr="")


@patch("lib.issue_wf.run", side_effect=_git)
class TestBuildPrompt(unittest.TestCase):
    def test_user_title_and_data_but_no_command(self, _run):
        p = _build_prompt(_INFO, title="bug x")
        self.assertIn("bug x", p)
        self.assertIn("<<<DATA>>>", p)
        self.assertIn("abc fix: y", p)
        self.assertNotIn("gh issue create", p)

    def test_no_title_shows_infers(self, _run):
        self.assertIn("推断", _build_prompt(_INFO, title=None))


@patch("lib.issue_wf.run", side_effect=_git)
class TestRunIssue(unittest.TestCase):
    def _run(self, info=_INFO, text="登录报错\n\n## Problem\n- 500", me="", **kw):
        with patch("lib.issue_wf.detect_provider", return_value=info), \
             patch("lib.issue_wf.detect_self_assignee", return_value=me), \
             patch("lib.ai_workflow.generate_via_lazygophers", return_value=text) as gen, \
             patch("lib.ai_workflow.retry_command", return_value=_OK) as retry, \
             patch("builtins.print"):
            rc = run_issue(**kw)
        return rc, retry, gen

    def test_gh_argv_from_api_response(self, _run):
        rc, retry, _ = self._run(me="myuser", labels="bug")
        self.assertEqual(rc, 0)
        self.assertEqual(retry.call_args.args[0], [
            "gh", "issue", "create", "--title", "登录报错", "--body", "## Problem\n- 500",
            "--label", "bug", "--assignee", "myuser"])

    def test_glab_argv_uses_description_and_milestone(self, _run):
        rc, retry, _ = self._run(info=_GLAB, milestone="m1")
        self.assertEqual(rc, 0)
        self.assertEqual(retry.call_args.args[0], [
            "glab", "issue", "create", "--title", "登录报错",
            "--description", "## Problem\n- 500", "--milestone", "m1"])

    def test_api_failure_exits_1_without_gh(self, _run):
        rc, retry, _ = self._run(text="")
        self.assertEqual(rc, 1)
        retry.assert_not_called()

    def test_dry_run(self, _run):
        rc, retry, gen = self._run(title="t", dry_run=True)
        self.assertEqual(rc, 0)
        gen.assert_not_called()
        retry.assert_not_called()

    @patch("lib.issue_wf.detect_provider", return_value=None)
    def test_no_provider(self, *_):
        self.assertEqual(run_issue(), 1)

    def test_missing_env_exits_1_without_gh(self, _run):
        import os
        env = {k: v for k, v in os.environ.items() if not k.startswith("LAZYGOPHERS_")}
        with patch("lib.issue_wf.detect_provider", return_value=_INFO), \
             patch("lib.issue_wf.detect_self_assignee", return_value=""), \
             patch.dict(os.environ, env, clear=True), \
             patch("lib.ai_workflow.urllib.request.urlopen") as urlopen, \
             patch("lib.ai_workflow.retry_command") as retry:
            self.assertEqual(run_issue("t"), 1)
        urlopen.assert_not_called()
        retry.assert_not_called()

    def test_api_error_exits_1_without_gh(self, _run):
        import os
        with patch("lib.issue_wf.detect_provider", return_value=_INFO), \
             patch("lib.issue_wf.detect_self_assignee", return_value=""), \
             patch.dict(os.environ, _ENV), \
             patch("lib.ai_workflow.run", return_value=MagicMock(returncode=0, stdout="abc\n")), \
             patch("lib.ai_workflow.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("refused")), \
             patch("lib.ai_workflow.retry_command") as retry:
            self.assertEqual(run_issue("t"), 1)
        retry.assert_not_called()


if __name__ == "__main__":
    unittest.main()

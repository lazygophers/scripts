#!/usr/bin/env python3
"""Tests for lib.mr_wf."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import urllib.error
from unittest.mock import MagicMock

from lib.ai_workflow import ProviderInfo
from lib.exec import RetryResult
from lib.mr_wf import _build_prompt, run_mr

_INFO = ProviderInfo(provider="gh", host="github.com", repo="o/r",
                     remote="origin", remote_url="git@github.com:o/r.git")
_GLAB = ProviderInfo(provider="glab", host="gitlab.com", repo="g/p",
                     remote="origin", remote_url="")
_ENV = {"LAZYGOPHERS_SCRIPTS_BASE_URL": "http://api", "LAZYGOPHERS_SCRIPTS_TOKEN": "t"}
_OK = RetryResult(ok=True, attempts=1, last_output="https://github.com/o/r/pull/1\n")


def _git(*_a, **_kw):
    return MagicMock(returncode=0, stdout="abc feat: x\n", stderr="")


class TestBuildPrompt(unittest.TestCase):
    @patch("lib.mr_wf.run", side_effect=_git)
    def test_has_data_and_format_but_no_command(self, _run):
        p = _build_prompt(_INFO, branch="feat", base="main")
        self.assertIn("<<<DATA>>>", p)
        self.assertIn("abc feat: x", p)
        self.assertIn("Summary", p)
        self.assertIn("第一行 title", p)
        self.assertNotIn("gh pr create", p)
        self.assertNotIn("执行", p.replace("勿执行", ""))


@patch("lib.mr_wf._find_existing_pr", return_value=None)
@patch("lib.mr_wf.run", side_effect=_git)
@patch("lib.mr_wf.remote_default_branch", return_value="main")
@patch("lib.mr_wf.current_branch", return_value="feat")
class TestRunMr(unittest.TestCase):
    def _run(self, info=_INFO, text="加缓存\n\n## Summary\n- x", me="", **kw):
        with patch("lib.mr_wf.detect_provider", return_value=info), \
             patch("lib.mr_wf.detect_self_assignee", return_value=me), \
             patch("lib.ai_workflow.generate_via_lazygophers", return_value=text) as gen, \
             patch("lib.ai_workflow.retry_command", return_value=_OK) as retry, \
             patch("builtins.print"):
            rc = run_mr(**kw)
        return rc, retry, gen

    def test_gh_argv_from_api_response(self, *_):
        rc, retry, _ = self._run(me="myuser", reviews="bob", labels="bug")
        self.assertEqual(rc, 0)
        self.assertEqual(retry.call_args.args[0], [
            "gh", "pr", "create", "--base", "main", "--title", "加缓存",
            "--body", "## Summary\n- x", "--draft",
            "--reviewer", "bob", "--label", "bug", "--assignee", "myuser"])

    def test_glab_argv_explicit_base_no_draft(self, *_):
        rc, retry, _ = self._run(info=_GLAB, base="develop", draft=False)
        self.assertEqual(rc, 0)
        self.assertEqual(retry.call_args.args[0], [
            "glab", "mr", "create", "--target-branch", "develop", "--title", "加缓存",
            "--description", "## Summary\n- x"])

    def test_api_failure_exits_1_without_gh(self, *_):
        rc, retry, _ = self._run(text="")
        self.assertEqual(rc, 1)
        retry.assert_not_called()

    def test_dry_run_generates_nothing(self, *_):
        rc, retry, gen = self._run(dry_run=True)
        self.assertEqual(rc, 0)
        gen.assert_not_called()
        retry.assert_not_called()

    @patch("lib.mr_wf.detect_provider", return_value=None)
    def test_no_provider(self, *_):
        self.assertEqual(run_mr(), 1)


@patch("lib.mr_wf._find_existing_pr", return_value=None)
@patch("lib.mr_wf.run", side_effect=_git)
@patch("lib.mr_wf.remote_default_branch", return_value="main")
@patch("lib.mr_wf.current_branch", return_value="feat")
@patch("lib.mr_wf.detect_self_assignee", return_value="")
@patch("lib.mr_wf.detect_provider", return_value=_INFO)
class TestRunMrRealApiPath(unittest.TestCase):
    """不 mock generate_via_lazygophers：env 缺失 / HTTP 失败都要 exit 1 且不跑 gh。"""

    def test_missing_env_exits_1_without_gh(self, *_):
        import os
        env = {k: v for k, v in os.environ.items() if not k.startswith("LAZYGOPHERS_")}
        with patch.dict(os.environ, env, clear=True), \
             patch("lib.ai_workflow.urllib.request.urlopen") as urlopen, \
             patch("lib.ai_workflow.retry_command") as retry:
            self.assertEqual(run_mr(), 1)
        urlopen.assert_not_called()
        retry.assert_not_called()

    def test_api_error_exits_1_without_gh(self, *_):
        import os
        with patch.dict(os.environ, _ENV), \
             patch("lib.ai_workflow.run", return_value=MagicMock(returncode=0, stdout="abc\n")), \
             patch("lib.ai_workflow.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("refused")), \
             patch("lib.ai_workflow.retry_command") as retry:
            self.assertEqual(run_mr(), 1)
        retry.assert_not_called()


if __name__ == "__main__":
    unittest.main()

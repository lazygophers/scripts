#!/usr/bin/env python3
"""Tests for lib.git_workflow (merge_to / push_to 转发 + run_workflow guard)。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import lib.git_workflow as gw


class TestMergeTo(unittest.TestCase):
    @patch.object(gw, "run_workflow", return_value=0)
    def test_forwards_args(self, mock_wf):
        rc = gw.merge_to("canary", argv=["merge_canary", "--dry-run"])
        self.assertEqual(rc, 0)
        mock_wf.assert_called_once()
        args, kwargs = mock_wf.call_args
        args[0]
        target = args[1]
        passthrough = args[2]
        self.assertEqual(target, "canary")
        self.assertEqual(passthrough, ["canary", "--dry-run"])
        self.assertTrue(kwargs["stay_on_target"])

    @patch.object(gw, "run_workflow", return_value=0)
    def test_default_script_name(self, mock_wf):
        gw.merge_to("dev", argv=["x"])
        script_name = mock_wf.call_args[0][0]
        self.assertIn("dev", script_name)

    @patch.object(gw, "run_workflow", return_value=0)
    def test_env_script_name_override(self, mock_wf):
        with patch.dict("os.environ", {"_SCRIPT_NAME": "custom"}):
            gw.merge_to("x", argv=["x"])
        self.assertEqual(mock_wf.call_args[0][0], "custom")

    @patch.object(gw, "run_workflow", return_value=7)
    def test_propagates_exit_code(self, mock_wf):
        self.assertEqual(gw.merge_to("x", argv=["x"]), 7)


class TestPushTo(unittest.TestCase):
    @patch.object(gw, "run_workflow", return_value=0)
    def test_forwards_stay_false_default(self, mock_wf):
        gw.push_to("canary", argv=["push_canary"])
        kwargs = mock_wf.call_args[1]
        self.assertFalse(kwargs["stay_on_target"])

    @patch.object(gw, "run_workflow", return_value=0)
    def test_stay_flag_detected(self, mock_wf):
        gw.push_to("canary", argv=["push_canary", "--stay"])
        kwargs = mock_wf.call_args[1]
        self.assertTrue(kwargs["stay_on_target"])

    @patch.object(gw, "run_workflow", return_value=0)
    def test_passthrough_includes_extra(self, mock_wf):
        gw.push_to("dev", argv=["push_develop", "--dry-run"])
        passthrough = mock_wf.call_args[0][2]
        self.assertEqual(passthrough, ["dev", "--dry-run"])


class TestRunWorkflowGuards(unittest.TestCase):
    @patch.object(gw, "_git")
    @patch.object(gw, "_resolve_target")
    def test_same_branch_skips(self, mock_resolve, mock_git):
        mock_resolve.return_value = ("main", None)
        # current branch == target → 跳过
        mock_git.return_value = MagicMock(returncode=0, stdout="main\n", stderr="")
        rc = gw.run_workflow("t", "main", ["t"])
        self.assertEqual(rc, 0)

    @patch.object(gw, "_git")
    @patch.object(gw, "_resolve_target")
    def test_dry_run_returns_zero(self, mock_resolve, mock_git):
        mock_resolve.return_value = ("main", None)
        mock_git.return_value = MagicMock(returncode=0, stdout="feature\n", stderr="")
        rc = gw.run_workflow("t", "main", ["t", "--dry-run"])
        self.assertEqual(rc, 0)

    @patch.object(gw, "_git")
    @patch.object(gw, "_resolve_target")
    def test_cannot_get_branch_returns_one(self, mock_resolve, mock_git):
        mock_resolve.return_value = ("main", None)
        mock_git.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        rc = gw.run_workflow("t", "main", ["t"])
        self.assertEqual(rc, 1)

    @patch.object(gw, "_git")
    @patch.object(gw, "update_branch", side_effect=gw.GitError("network"))
    @patch.object(gw, "_resolve_target")
    def test_sync_failure_returns_one(self, mock_resolve, mock_update, mock_git):
        mock_resolve.return_value = ("main", None)
        mock_git.return_value = MagicMock(returncode=0, stdout="feature\n", stderr="")
        rc = gw.run_workflow("t", "main", ["t"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()

"""lib/git_workflow.py 的流程测试。

tests/test_git_workflow.py 只覆盖 merge_to / push_to 的参数转发和几个早退分支；
这里把 _git 换成一个可编程的假 git，跑通 run_workflow / run_merge_workflow 的
正常路径与各条失败路径，以及 _ensure_remote_branch_exists、_preview_merge_conflicts、
_gate_check_build、_resolve_target 这些辅助函数。

所有 git 调用都是假的，不碰真仓库。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lib.git_workflow as gw  # noqa: E402
from lib.build import BuildError, CheckResult  # noqa: E402


def _p(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeGit:
    """按子命令前缀匹配的假 git。

    rules 的 key 是空格拼接的参数前缀（如 "merge --no-edit"），最长前缀优先；
    没命中的调用一律返回 returncode=0。calls 里留下全部调用参数供断言。
    """

    def __init__(self, rules: dict[str, SimpleNamespace] | None = None) -> None:
        self.rules = rules or {}
        self.calls: list[list[str]] = []

    def __call__(self, args, **_kwargs):
        self.calls.append(list(args))
        joined = " ".join(args)
        best = None
        for prefix, result in self.rules.items():
            if joined.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
                best = (prefix, result)
        return best[1] if best else _p(0)

    def ran(self, prefix: str) -> bool:
        return any(" ".join(c).startswith(prefix) for c in self.calls)


class FlowCase(unittest.TestCase):
    """把工作流里所有会碰到外部世界的东西都钉死。"""

    def setUp(self) -> None:
        patches = [
            mock.patch.object(gw, "notify_via_n"),
            mock.patch.object(gw, "check_bit_clean"),
            mock.patch.object(gw, "update_branch"),
            mock.patch.object(gw, "reporter", return_value=mock.MagicMock()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class TestNotifyDone(unittest.TestCase):
    def test_batch_mode_stays_silent(self) -> None:
        with mock.patch.dict("os.environ", {"_GITWF_BATCH": "1"}), \
             mock.patch.object(gw, "notify_via_n") as n:
            gw._notify_done("done", script_dir=Path("."))
        n.assert_not_called()

    def test_non_batch_mode_speaks(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(gw, "notify_via_n") as n:
            gw._notify_done("done", script_dir=Path("."))
        n.assert_called_once()


class TestStepCounter(unittest.TestCase):
    def test_step_numbers_increase(self) -> None:
        r = mock.MagicMock()
        gw._STEP_COUNTER = 0
        gw._step("a", r)
        gw._step("b", r)
        self.assertEqual(r.step.call_args_list[0][0][0], "[1] a")
        self.assertEqual(r.step.call_args_list[1][0][0], "[2] b")


class TestGateCheckBuild(unittest.TestCase):
    def test_all_green_passes(self) -> None:
        ok = CheckResult(name="go", status="ok", message="")
        with mock.patch.object(gw, "check_build", return_value=[ok]):
            gw._gate_check_build(mock.MagicMock(), where="当前分支 ")

    def test_build_error_becomes_git_error(self) -> None:
        with mock.patch.object(gw, "check_build", side_effect=BuildError("go 编译失败")):
            with self.assertRaises(gw.GitError) as cm:
                gw._gate_check_build(mock.MagicMock(), where="当前分支 ")
        self.assertIn("go 编译失败", str(cm.exception))

    def test_fail_status_also_blocks(self) -> None:
        bad = CheckResult(name="rust", status="fail", message="cargo check 失败")
        with mock.patch.object(gw, "check_build", return_value=[bad]):
            with self.assertRaises(gw.GitError) as cm:
                gw._gate_check_build(mock.MagicMock(), where="合并结果 ")
        self.assertIn("cargo check 失败", str(cm.exception))


class TestRemoteHelpers(unittest.TestCase):
    def test_remote_branch_exists_reads_exit_code(self) -> None:
        with mock.patch.object(gw, "_git", return_value=_p(0)):
            self.assertTrue(gw._remote_branch_exists("main"))
        with mock.patch.object(gw, "_git", return_value=_p(2)):
            self.assertFalse(gw._remote_branch_exists("main"))

    def test_remote_head_branch_strips_remote_prefix(self) -> None:
        with mock.patch.object(gw, "_git", return_value=_p(0, "origin/main\n")):
            self.assertEqual(gw._remote_head_branch(), "main")

    def test_remote_head_branch_without_slash(self) -> None:
        with mock.patch.object(gw, "_git", return_value=_p(0, "main\n")):
            self.assertEqual(gw._remote_head_branch(), "main")

    def test_remote_head_branch_empty_is_none(self) -> None:
        with mock.patch.object(gw, "_git", return_value=_p(1, "")):
            self.assertIsNone(gw._remote_head_branch())


class TestEnsureRemoteBranchExists(unittest.TestCase):
    def test_existing_branch_short_circuits(self) -> None:
        with mock.patch.object(gw, "_remote_branch_exists", return_value=True) as ex, \
             mock.patch.object(gw, "_git") as g:
            self.assertTrue(gw._ensure_remote_branch_exists("canary"))
        ex.assert_called_once()
        g.assert_not_called()

    def test_creates_local_then_pushes(self) -> None:
        fake = FakeGit()
        # target 不存在，base(main) 存在
        exists = {"canary": False, "main": True}
        with mock.patch.object(gw, "_remote_branch_exists", side_effect=lambda b, **k: exists.get(b, False)), \
             mock.patch.object(gw, "_remote_head_branch", return_value="main"), \
             mock.patch.object(gw, "_git", fake):
            # rev-parse --verify 返回 0 表示本地已有 → 走另一条分支，这里让它失败
            fake.rules["rev-parse"] = _p(1)
            ok = gw._ensure_remote_branch_exists("canary", r=mock.MagicMock())
        self.assertTrue(ok)
        self.assertTrue(fake.ran("branch canary origin/main"))
        self.assertTrue(fake.ran("push -u origin canary"))

    def test_keeps_existing_local_ref(self) -> None:
        fake = FakeGit({"rev-parse": _p(0)})
        with mock.patch.object(gw, "_remote_branch_exists", side_effect=lambda b, **k: b == "main"), \
             mock.patch.object(gw, "_remote_head_branch", return_value="main"), \
             mock.patch.object(gw, "_git", fake):
            ok = gw._ensure_remote_branch_exists("canary", r=mock.MagicMock())
        self.assertTrue(ok)
        self.assertFalse(fake.ran("branch canary"))
        self.assertTrue(fake.ran("push -u origin canary"))

    def test_falls_back_to_enumerating_main_master(self) -> None:
        fake = FakeGit({"rev-parse": _p(1)})
        with mock.patch.object(gw, "_remote_branch_exists", side_effect=lambda b, **k: b == "master"), \
             mock.patch.object(gw, "_remote_head_branch", return_value=None), \
             mock.patch.object(gw, "_git", fake):
            gw._ensure_remote_branch_exists("canary", r=mock.MagicMock())
        self.assertTrue(fake.ran("branch canary origin/master"))

    def test_local_branch_creation_failure_returns_false(self) -> None:
        fake = FakeGit({"rev-parse": _p(1), "branch canary": _p(1)})
        with mock.patch.object(gw, "_remote_branch_exists", side_effect=lambda b, **k: b == "main"), \
             mock.patch.object(gw, "_remote_head_branch", return_value="main"), \
             mock.patch.object(gw, "_git", fake):
            self.assertFalse(gw._ensure_remote_branch_exists("canary", r=mock.MagicMock()))

    def test_push_failure_returns_false(self) -> None:
        fake = FakeGit({"rev-parse": _p(0), "push -u": _p(1)})
        with mock.patch.object(gw, "_remote_branch_exists", side_effect=lambda b, **k: b == "main"), \
             mock.patch.object(gw, "_remote_head_branch", return_value="main"), \
             mock.patch.object(gw, "_git", fake):
            self.assertFalse(gw._ensure_remote_branch_exists("canary", r=mock.MagicMock()))


class TestPreviewMergeConflicts(unittest.TestCase):
    def test_merge_tree_zero_means_clean(self) -> None:
        with mock.patch.object(gw, "_git", return_value=_p(0)):
            self.assertFalse(gw._preview_merge_conflicts("main", "feat"))

    def test_merge_tree_one_means_conflict(self) -> None:
        with mock.patch.object(gw, "_git", return_value=_p(1)):
            self.assertTrue(gw._preview_merge_conflicts("main", "feat"))

    def test_old_git_falls_back_to_no_commit_probe(self) -> None:
        fake = FakeGit({
            "merge-tree": _p(129, stderr="unknown option --write-tree"),
            "merge --no-commit": _p(1),
        })
        with mock.patch.object(gw, "_git", fake):
            self.assertTrue(gw._preview_merge_conflicts("main", "feat", r=mock.MagicMock()))
        self.assertTrue(fake.ran("merge --abort"))

    def test_fallback_probe_clean_reports_no_conflict(self) -> None:
        fake = FakeGit({"merge-tree": _p(129), "merge --no-commit": _p(0)})
        with mock.patch.object(gw, "_git", fake):
            self.assertFalse(gw._preview_merge_conflicts("main", "feat", r=mock.MagicMock()))
        self.assertTrue(fake.ran("merge --abort"))


class TestResolveTarget(unittest.TestCase):
    def test_without_auto_detect_uses_arg(self) -> None:
        self.assertEqual(gw._resolve_target("dev"), ("dev", None))

    def test_without_auto_detect_defaults_to_canary(self) -> None:
        self.assertEqual(gw._resolve_target(None), ("canary", None))

    def test_auto_detect_uses_remote_head(self) -> None:
        with mock.patch.object(gw, "_remote_head_branch", return_value="main"):
            self.assertEqual(gw._resolve_target(None, auto_detect=True), ("main", "main"))

    def test_auto_detect_keeps_explicit_arg(self) -> None:
        with mock.patch.object(gw, "_remote_head_branch", return_value="main"):
            self.assertEqual(gw._resolve_target("dev", auto_detect=True), ("dev", "main"))

    def test_auto_detect_without_remote_head_raises(self) -> None:
        with mock.patch.object(gw, "_remote_head_branch", return_value=None):
            with self.assertRaises(gw.GitError):
                gw._resolve_target(None, auto_detect=True)


class TestRunWorkflow(FlowCase):
    """push_* 方向：current → target，推送后切回。"""

    def _run(self, fake: FakeGit, argv: list[str], *, stay: bool = False,
             push_ok: bool = True, build_ok: bool = True, tty: bool = False) -> int:
        gate = mock.DEFAULT if build_ok else mock.Mock(side_effect=gw.GitError("构建检查失败"))
        with mock.patch.object(gw, "_git", fake), \
             mock.patch.object(gw, "_resolve_target", return_value=("canary", None)), \
             mock.patch.object(gw, "_ensure_remote_branch_exists", return_value=True), \
             mock.patch.object(gw, "_gate_check_build",
                               side_effect=None if build_ok else gw.GitError("构建检查失败")), \
             mock.patch.object(gw, "retry_command",
                               return_value=SimpleNamespace(ok=push_ok, last_output="pushed")), \
             mock.patch.object(gw.sys.stdin, "isatty", return_value=tty):
            del gate
            return gw.run_workflow("push_canary", "canary", argv, stay_on_target=stay)

    def test_happy_path_merges_pushes_and_switches_back(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        rc = self._run(fake, ["push_canary"])
        self.assertEqual(rc, 0)
        self.assertTrue(fake.ran("merge --no-edit feature"))
        self.assertTrue(fake.ran("checkout feature"))

    def test_stay_on_target_skips_switch_back(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        rc = self._run(fake, ["push_canary"], stay=True)
        self.assertEqual(rc, 0)
        self.assertFalse(fake.ran("checkout feature"))

    def test_no_check_skips_both_gates(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        with mock.patch.object(gw, "_gate_check_build") as gate:
            with mock.patch.object(gw, "_git", fake), \
                 mock.patch.object(gw, "_resolve_target", return_value=("canary", None)), \
                 mock.patch.object(gw, "_ensure_remote_branch_exists", return_value=True), \
                 mock.patch.object(gw, "retry_command",
                                   return_value=SimpleNamespace(ok=True, last_output="")):
                rc = gw.run_workflow("push_canary", "canary", ["push_canary", "--no-check"])
        self.assertEqual(rc, 0)
        gate.assert_not_called()

    def test_build_gate_failure_returns_one(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        self.assertEqual(self._run(fake, ["push_canary"], build_ok=False), 1)

    def test_dry_run_lists_steps_without_touching_git(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        rc = self._run(fake, ["push_canary", "--dry-run", "--auto-commit"])
        self.assertEqual(rc, 0)
        self.assertFalse(fake.ran("merge"))

    def test_dry_run_with_no_check_still_returns_zero(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        self.assertEqual(self._run(fake, ["push_canary", "--dry-run", "--no-check"]), 0)

    def test_preview_conflict_aborts_before_merging(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n"), "merge-tree": _p(1)})
        rc = self._run(fake, ["push_canary"])
        self.assertEqual(rc, 1)
        self.assertFalse(fake.ran("merge --no-edit"))

    def test_merge_conflict_non_interactive_aborts(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n"), "merge --no-edit": _p(1)})
        rc = self._run(fake, ["push_canary"], tty=False)
        self.assertEqual(rc, 1)

    def test_merge_conflict_interactive_resolved_by_user(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n"), "merge --no-edit": _p(1)})
        with mock.patch("builtins.input", return_value=""):
            rc = self._run(fake, ["push_canary"], tty=True)
        self.assertEqual(rc, 0)
        self.assertTrue(fake.ran("add ."))
        self.assertTrue(fake.ran("commit --no-edit"))

    def test_merge_conflict_interactive_commit_failure_aborts(self) -> None:
        fake = FakeGit({
            "branch --show-current": _p(0, "feature\n"),
            "merge --no-edit": _p(1),
            "commit --no-edit": _p(1),
        })
        with mock.patch("builtins.input", return_value=""):
            rc = self._run(fake, ["push_canary"], tty=True)
        self.assertEqual(rc, 1)

    def test_push_failure_returns_one(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        self.assertEqual(self._run(fake, ["push_canary"], push_ok=False), 1)

    def test_missing_remote_target_creation_failure_returns_one(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        with mock.patch.object(gw, "_git", fake), \
             mock.patch.object(gw, "_resolve_target", return_value=("canary", None)), \
             mock.patch.object(gw, "_ensure_remote_branch_exists", return_value=False), \
             mock.patch.object(gw, "_gate_check_build"):
            rc = gw.run_workflow("push_canary", "canary", ["push_canary"])
        self.assertEqual(rc, 1)

    def test_auto_commit_runs_commit_when_dirty(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        with mock.patch("lib.commit_wf._has_changes", return_value=(True, "M x")), \
             mock.patch("lib.commit_wf.run_commit", return_value=0) as rc_commit:
            rc = self._run(fake, ["push_canary", "--auto-commit"])
        self.assertEqual(rc, 0)
        rc_commit.assert_called_once()

    def test_auto_commit_failure_aborts(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        with mock.patch("lib.commit_wf._has_changes", return_value=(True, "M x")), \
             mock.patch("lib.commit_wf.run_commit", return_value=1):
            rc = self._run(fake, ["push_canary", "--auto-commit"])
        self.assertEqual(rc, 1)

    def test_auto_commit_noop_when_clean(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        with mock.patch("lib.commit_wf._has_changes", return_value=(False, "")), \
             mock.patch("lib.commit_wf.run_commit") as rc_commit:
            rc = self._run(fake, ["push_canary", "--auto-commit"])
        self.assertEqual(rc, 0)
        rc_commit.assert_not_called()

    def test_finally_block_switches_back_when_left_elsewhere(self) -> None:
        # 结尾 branch --show-current 仍报 canary → finally 里兜底 checkout 回 feature
        outputs = iter(["feature\n"] + ["canary\n"] * 10)
        fake = FakeGit()

        def show_current(args, **kwargs):
            fake.calls.append(list(args))
            if " ".join(args).startswith("branch --show-current"):
                return _p(0, next(outputs))
            return _p(0)

        with mock.patch.object(gw, "_git", show_current), \
             mock.patch.object(gw, "_resolve_target", return_value=("canary", None)), \
             mock.patch.object(gw, "_ensure_remote_branch_exists", return_value=True), \
             mock.patch.object(gw, "_gate_check_build"), \
             mock.patch.object(gw, "retry_command",
                               return_value=SimpleNamespace(ok=True, last_output="")):
            rc = gw.run_workflow("push_canary", "canary", ["push_canary"])
        self.assertEqual(rc, 0)
        self.assertTrue(fake.ran("checkout feature"))

    def test_remote_default_hint_is_reported(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "main\n")})
        r = mock.MagicMock()
        with mock.patch.object(gw, "_git", fake), \
             mock.patch.object(gw, "reporter", return_value=r), \
             mock.patch.object(gw, "_resolve_target", return_value=("main", "main")):
            rc = gw.run_workflow("push_master", "master", ["push_master"])
        self.assertEqual(rc, 0)  # current == target → 早退
        r.kv.assert_called_once()


class TestRunMergeWorkflow(FlowCase):
    """merge_* 方向：target → current，留在当前分支。"""

    def _run(self, fake: FakeGit, argv: list[str], *, remote_has_current: bool = True,
             confirm_stop: bool = True, tty: bool = False) -> int:
        with mock.patch.object(gw, "_git", fake), \
             mock.patch.object(gw, "_resolve_target", return_value=("canary", None)), \
             mock.patch.object(gw, "_ensure_remote_branch_exists", return_value=True), \
             mock.patch.object(gw, "_remote_branch_exists", return_value=remote_has_current), \
             mock.patch("lib.ui.ask_confirm", return_value=confirm_stop), \
             mock.patch.object(gw.sys.stdin, "isatty", return_value=tty):
            return gw.run_merge_workflow("merge_canary", "canary", argv)

    def test_happy_path_merges_target_into_current(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        rc = self._run(fake, ["merge_canary"])
        self.assertEqual(rc, 0)
        self.assertTrue(fake.ran("merge --no-edit canary"))

    def test_same_branch_skips(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "canary\n")})
        rc = self._run(fake, ["merge_canary"])
        self.assertEqual(rc, 0)
        self.assertFalse(fake.ran("merge --no-edit"))

    def test_cannot_read_current_branch_returns_one(self) -> None:
        fake = FakeGit({"branch --show-current": _p(1, "", "fatal")})
        self.assertEqual(self._run(fake, ["merge_canary"]), 1)

    def test_dry_run_returns_zero(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        rc = self._run(fake, ["merge_canary", "--dry-run", "--auto-commit"])
        self.assertEqual(rc, 0)
        self.assertFalse(fake.ran("merge --no-edit"))

    def test_missing_remote_current_branch_skips_sync(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        with mock.patch.object(gw, "update_branch") as ub:
            rc = self._run(fake, ["merge_canary"], remote_has_current=False)
        self.assertEqual(rc, 0)
        # 只同步了目标分支，没同步当前分支
        self.assertEqual([c[0][0] for c in ub.call_args_list], ["canary"])

    def test_conflict_preview_user_stops(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n"), "merge-tree": _p(1)})
        rc = self._run(fake, ["merge_canary"], confirm_stop=False)
        self.assertEqual(rc, 1)
        self.assertFalse(fake.ran("merge --no-edit"))

    def test_conflict_preview_user_continues(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n"), "merge-tree": _p(1)})
        rc = self._run(fake, ["merge_canary"], confirm_stop=True)
        self.assertEqual(rc, 0)
        self.assertTrue(fake.ran("merge --no-edit canary"))

    def test_conflict_preview_no_answer_is_treated_as_stop(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n"), "merge-tree": _p(1)})
        rc = self._run(fake, ["merge_canary"], confirm_stop=None)
        self.assertEqual(rc, 1)

    def test_merge_conflict_non_interactive_aborts_merge(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n"), "merge --no-edit": _p(1)})
        rc = self._run(fake, ["merge_canary"], tty=False)
        self.assertEqual(rc, 1)
        self.assertTrue(fake.ran("merge --abort"))

    def test_merge_conflict_interactive_resolved(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n"), "merge --no-edit": _p(1)})
        with mock.patch("builtins.input", return_value=""):
            rc = self._run(fake, ["merge_canary"], tty=True)
        self.assertEqual(rc, 0)
        self.assertTrue(fake.ran("add ."))

    def test_merge_conflict_interactive_commit_failure_aborts(self) -> None:
        fake = FakeGit({
            "branch --show-current": _p(0, "feature\n"),
            "merge --no-edit": _p(1),
            "commit --no-edit": _p(1),
        })
        with mock.patch("builtins.input", return_value=""):
            rc = self._run(fake, ["merge_canary"], tty=True)
        self.assertEqual(rc, 1)
        self.assertTrue(fake.ran("merge --abort"))

    def test_target_branch_creation_failure_returns_one(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        with mock.patch.object(gw, "_git", fake), \
             mock.patch.object(gw, "_resolve_target", return_value=("canary", None)), \
             mock.patch.object(gw, "_remote_branch_exists", return_value=True), \
             mock.patch.object(gw, "_ensure_remote_branch_exists", return_value=False):
            rc = gw.run_merge_workflow("merge_canary", "canary", ["merge_canary"])
        self.assertEqual(rc, 1)

    def test_dirty_worktree_returns_one(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        with mock.patch.object(gw, "check_bit_clean", side_effect=gw.GitError("工作区不干净")):
            rc = self._run(fake, ["merge_canary"])
        self.assertEqual(rc, 1)

    def test_auto_commit_runs_commit_when_dirty(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        with mock.patch("lib.commit_wf._has_changes", return_value=(True, "M x")), \
             mock.patch("lib.commit_wf.run_commit", return_value=0) as rc_commit:
            rc = self._run(fake, ["merge_canary", "--auto-commit"])
        self.assertEqual(rc, 0)
        rc_commit.assert_called_once()

    def test_auto_commit_failure_aborts(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "feature\n")})
        with mock.patch("lib.commit_wf._has_changes", return_value=(True, "M x")), \
             mock.patch("lib.commit_wf.run_commit", return_value=2):
            rc = self._run(fake, ["merge_canary", "--auto-commit"])
        self.assertEqual(rc, 1)

    def test_switches_back_when_update_branch_left_on_target(self) -> None:
        # update_branch 可能停在 target 上：第 2 次 show-current 报 canary → 应 checkout 回 feature
        seq = iter(["feature\n", "canary\n", "feature\n", "feature\n"])
        fake = FakeGit()

        def git(args, **kwargs):
            fake.calls.append(list(args))
            if " ".join(args).startswith("branch --show-current"):
                return _p(0, next(seq, "feature\n"))
            return _p(0)

        with mock.patch.object(gw, "_git", git), \
             mock.patch.object(gw, "_resolve_target", return_value=("canary", None)), \
             mock.patch.object(gw, "_remote_branch_exists", return_value=True), \
             mock.patch.object(gw, "_ensure_remote_branch_exists", return_value=True), \
             mock.patch("lib.ui.ask_confirm", return_value=True):
            rc = gw.run_merge_workflow("merge_canary", "canary", ["merge_canary"])
        self.assertEqual(rc, 0)
        self.assertTrue(fake.ran("checkout feature"))

    def test_remote_default_hint_is_reported(self) -> None:
        fake = FakeGit({"branch --show-current": _p(0, "main\n")})
        r = mock.MagicMock()
        with mock.patch.object(gw, "_git", fake), \
             mock.patch.object(gw, "reporter", return_value=r), \
             mock.patch.object(gw, "_resolve_target", return_value=("main", "main")):
            rc = gw.run_merge_workflow("merge_master", "master", ["merge_master"])
        self.assertEqual(rc, 0)
        r.kv.assert_called_once()


class TestGitShim(unittest.TestCase):
    def test_git_prepends_the_git_binary(self) -> None:
        with mock.patch.object(gw, "run_logged", return_value=_p(0)) as rl:
            gw._git(["status", "--short"], title="状态")
        self.assertEqual(rl.call_args[0][0], ["git", "status", "--short"])
        self.assertFalse(rl.call_args[1]["check"])


class TestThinWrappers(unittest.TestCase):
    def test_merge_to_uses_sys_argv_by_default(self) -> None:
        with mock.patch.object(gw.sys, "argv", ["merge_canary", "--dry-run"]), \
             mock.patch.object(gw, "run_merge_workflow", return_value=0) as wf:
            gw.merge_to("canary")
        self.assertEqual(wf.call_args[0][2], ["canary", "--dry-run"])

    def test_push_to_uses_sys_argv_by_default(self) -> None:
        with mock.patch.object(gw.sys, "argv", ["push_canary", "--stay"]), \
             mock.patch.object(gw, "run_workflow", return_value=0) as wf:
            gw.push_to("canary")
        self.assertTrue(wf.call_args[1]["stay_on_target"])


if __name__ == "__main__":
    unittest.main()

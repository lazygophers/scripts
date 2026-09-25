"""lib/batch_git.py 补测：汇总/通知的文案组合、worktree 探测、push 自愈、
各 factory 的失败与跳过分支。

git 调用一律走假的 `_run` / `_run_exec`（复用 tests/test_batch_git_runner.py 的
FakeRun），不碰真仓库、不连远端。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lib.batch_git as bg  # noqa: E402
from lib.batch_git import BatchResult, BatchRunner, CallbackBatchOperation, RepoPlan, RepoResult  # noqa: E402
from tests.test_batch_git_runner import FakeRun, RunnerCase, _cp, _r  # noqa: E402

REPO = Path("/repo")
ROOT = Path("/root")


def result_of(*, ok=0, skip=0, fail=0) -> BatchResult:
    """造一个只有计数有意义的 BatchResult。"""
    def rows(n, tag):
        return [RepoResult(name=f"{tag}{i}", path=f"/r/{tag}{i}", status=tag) for i in range(n)]

    res = BatchResult(total=ok + skip + fail)
    res.succeeded.extend(rows(ok, "ok"))
    res.skipped.extend(rows(skip, "skip"))
    res.failed.extend(rows(fail, "fail"))
    return res


def run_with(fake: FakeRun, fn, *args, **kwargs):
    """在假 _run / _run_exec 下跑一段 batch_git 代码。"""
    def fake_exec(cmd, *, label="", r=None, **kw):
        return fake(cmd, **kw)

    with mock.patch.object(bg, "_run", fake), mock.patch.object(bg, "_run_exec", fake_exec):
        return fn(*args, **kwargs)


def exec_plan(fake: FakeRun, detect, repo: Path = REPO):
    """detect → execute 一条龙，两段都在假 _run 下跑。"""
    def go():
        plan = detect(repo, _r(), ROOT)
        if plan.execute is None:
            return plan.status, plan.detail
        return plan.execute(repo, plan, _r(), ROOT)

    return run_with(fake, go)


class TestPrintSummaryFooter(unittest.TestCase):
    """footer：三种计数各自带色，一个都没有时退回「共 N 个」。"""

    def _footer(self, result: BatchResult):
        r = mock.MagicMock()
        bg.print_summary(r, "标题", result)
        return r.status_footer.call_args.args[0]

    def test_empty_result_shows_total(self):
        self.assertEqual(self._footer(BatchResult(total=7)), [("共 7 个", "cyan")])

    def test_all_three_counts_are_listed(self):
        parts = self._footer(result_of(ok=1, skip=2, fail=3))
        self.assertEqual([text for text, _ in parts],
                         ["失败 3/6", "成功 1/6", "跳过 2/6"])
        self.assertEqual([color for _, color in parts], ["red", "green", "yellow"])


class TestNotifyBatchDone(unittest.TestCase):
    """通知文案按 成功/跳过/失败 的组合精确措辞，不能笼统说「完成」。"""

    def _msg(self, **counts) -> str:
        with mock.patch("lib.notify.notify_via_n") as notify:
            bg.notify_batch_done("myrepos", result_of(**counts), script_dir=Path("."))
        return notify.call_args.args[0]

    def test_partial_failure_with_skips(self):
        self.assertEqual(self._msg(ok=2, skip=1, fail=3),
                         "myrepos 部分失败：成功 2、跳过 1、失败 3")

    def test_partial_failure_without_skips(self):
        self.assertEqual(self._msg(ok=2, fail=3), "myrepos 部分失败：成功 2、失败 3")

    def test_only_failures(self):
        self.assertEqual(self._msg(fail=2), "myrepos 失败 2 个")

    def test_failures_and_skips(self):
        self.assertEqual(self._msg(skip=1, fail=2), "myrepos 失败 2 个、跳过 1")

    def test_success_and_skips(self):
        self.assertEqual(self._msg(ok=2, skip=1), "myrepos 成功 2、跳过 1")

    def test_only_success(self):
        self.assertEqual(self._msg(ok=2), "myrepos 成功 2 个")

    def test_all_skipped_is_not_called_done(self):
        self.assertEqual(self._msg(skip=3), "myrepos 全部跳过（3 个）")

    def test_no_repos_at_all(self):
        self.assertEqual(self._msg(), "myrepos 无仓库可处理")


class _NoneDetectOp(CallbackBatchOperation):
    """detect 返回 None 的操作（调度器要自己兜住，不能崩）。"""

    def detect(self, repo, r, root):
        return None


class _OpLevelExecuteOp(CallbackBatchOperation):
    """plan 不带 execute、由 operation.execute 统一执行的操作。"""

    def __init__(self, outcome: RepoPlan, **kw):
        super().__init__(**kw)
        self._outcome = outcome

    def detect(self, repo, r, root):
        return RepoPlan(status="ok", detail="待执行")

    def execute(self, repo, plan, r, root):
        return self._outcome


class TestRunnerEdges(RunnerCase):
    """调度器的兜底：detect 返回 None、operation 级 execute、Ctrl-C。"""

    def _run(self, op, repos):
        op.root = ROOT
        with mock.patch.object(op, "scan", return_value=repos), \
             mock.patch.object(bg, "notify_batch_done"):
            return BatchRunner().run(op)

    def test_detect_returning_none_becomes_a_failure(self):
        op = _NoneDetectOp(title="t", root=ROOT)
        result = self._run(op, [ROOT / "a"])
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.failed[0].detail, "detect 返回 None")

    def test_operation_level_execute_counts_as_success(self):
        op = _OpLevelExecuteOp(RepoPlan(status="ok", detail="做完了"), title="t", root=ROOT)
        result = self._run(op, [ROOT / "a"])
        self.assertEqual([x.detail for x in result.succeeded], ["做完了"])

    def test_operation_level_execute_counts_skip_and_fail(self):
        op = _OpLevelExecuteOp(RepoPlan(status="skip", detail="没必要"), title="t", root=ROOT)
        self.assertEqual(len(self._run(op, [ROOT / "a"]).skipped), 1)
        op = _OpLevelExecuteOp(RepoPlan(status="fail", detail="炸了"), title="t", root=ROOT)
        self.assertEqual(len(self._run(op, [ROOT / "a"]).failed), 1)

    def test_ctrl_c_during_serial_execute_exits_130(self):
        def boom(*_a, **_kw):
            raise KeyboardInterrupt

        op = CallbackBatchOperation(
            title="t", root=ROOT,
            detect_fn=lambda repo, r, root: RepoPlan(status="ok", execute=boom))
        with mock.patch.object(bg.os, "_exit") as exit_now:
            self._run(op, [ROOT / "a"])
        exit_now.assert_called_with(130)

    def test_ctrl_c_during_parallel_execute_exits_130(self):
        op = CallbackBatchOperation(
            title="t", root=ROOT,
            detect_fn=lambda repo, r, root: RepoPlan(status="ok",
                                                     execute=lambda *a: ("ok", "")))
        real = bg.as_completed
        rounds = {"n": 0}

        def flaky(futures):
            rounds["n"] += 1
            if rounds["n"] == 1:      # 第一轮是 detect 段，要让它正常跑完
                return real(futures)
            raise KeyboardInterrupt   # 第二轮（执行段）模拟 Ctrl-C

        with mock.patch.dict("os.environ", {"EXECUTE_CONCURRENCY": "2"}), \
             mock.patch.object(bg, "as_completed", flaky), \
             mock.patch.object(bg.os, "_exit") as exit_now:
            self._run(op, [ROOT / "a", ROOT / "b"])
        exit_now.assert_called_with(130)


class TestRunnerProgress(RunnerCase):
    """进度条存在时（人类终端）：每跑完一个仓库推进一格；Ctrl-C 先停进度条再退。"""

    def _run(self, op, repos, prog):
        op.root = ROOT
        with mock.patch.object(op, "scan", return_value=repos), \
             mock.patch.object(bg, "progress", return_value=prog), \
             mock.patch.object(bg, "notify_batch_done"):
            return BatchRunner().run(op)

    def test_parallel_execute_advances_the_bar(self):
        prog = mock.MagicMock()
        op = CallbackBatchOperation(
            title="t", root=ROOT,
            detect_fn=lambda repo, r, root: RepoPlan(status="ok",
                                                     execute=lambda *a: ("ok", "")))
        with mock.patch.dict("os.environ", {"EXECUTE_CONCURRENCY": "2"}):
            result = self._run(op, [ROOT / "a", ROOT / "b"], prog)
        self.assertEqual(len(result.succeeded), 2)
        self.assertGreaterEqual(prog.advance.call_count, 2)

    def test_serial_ctrl_c_stops_the_bar_before_exiting(self):
        prog = mock.MagicMock()

        def boom(*_a, **_kw):
            raise KeyboardInterrupt

        op = CallbackBatchOperation(
            title="t", root=ROOT,
            detect_fn=lambda repo, r, root: RepoPlan(status="ok", execute=boom))
        with mock.patch.object(bg.os, "_exit") as exit_now:
            self._run(op, [ROOT / "a"], prog)
        exit_now.assert_called_with(130)
        self.assertTrue(prog.stop.called)


class TestWorktreeProbe(unittest.TestCase):
    """_branch_worktree_path(_live)：占用探测 + 陈旧 worktree 的 prune 重试。"""

    LIST = "worktree /wt/feature\nHEAD abc\nbranch refs/heads/feature\n"

    def test_list_failure_means_not_occupied(self):
        fake = FakeRun({"git worktree list": _cp(128, stderr="not a repo")})
        self.assertEqual(run_with(fake, bg._branch_worktree_path, REPO, "feature"), "")

    def test_occupied_branch_returns_the_path(self):
        fake = FakeRun({"git worktree list": _cp(0, stdout=self.LIST)})
        self.assertEqual(run_with(fake, bg._branch_worktree_path, REPO, "feature"), "/wt/feature")

    def test_other_branch_is_not_matched(self):
        fake = FakeRun({"git worktree list": _cp(0, stdout=self.LIST)})
        self.assertEqual(run_with(fake, bg._branch_worktree_path, REPO, "别的"), "")

    def test_live_probe_short_circuits_when_unused(self):
        fake = FakeRun({"git worktree list": _cp(0, stdout="")})
        self.assertEqual(run_with(fake, bg._branch_worktree_path_live, REPO, "feature"), "")

    def test_live_probe_returns_a_healthy_worktree(self):
        fake = FakeRun({"git worktree list": _cp(0, stdout=self.LIST),
                        "git -C /wt/feature rev-parse": _cp(0)})
        self.assertEqual(run_with(fake, bg._branch_worktree_path_live, REPO, "feature"),
                         "/wt/feature")

    def test_stale_worktree_is_pruned_then_gone(self):
        # 目录已经没了 → prune 之后 list 也不再报它
        state = {"pruned": False}

        class Pruning(FakeRun):
            def __call__(self, cmd, **kw):
                joined = " ".join(cmd)
                if joined.startswith("git worktree prune"):
                    state["pruned"] = True
                    return _cp(0)
                if joined.startswith("git worktree list"):
                    return _cp(0, stdout="" if state["pruned"] else TestWorktreeProbe.LIST)
                if joined.startswith("git -C /wt/feature rev-parse"):
                    return _cp(128)
                return _cp(0)

        self.assertEqual(run_with(Pruning(), bg._branch_worktree_path_live, REPO, "feature"), "")
        self.assertTrue(state["pruned"])

    def test_prune_makes_the_worktree_readable_again(self):
        # prune 之后 list 还报它，这次探测通了 → 认它占用
        probes = {"n": 0}

        class Recovering(FakeRun):
            def __call__(self, cmd, **kw):
                joined = " ".join(cmd)
                self.calls.append(list(cmd))
                if joined.startswith("git worktree list"):
                    return _cp(0, stdout=TestWorktreeProbe.LIST)
                if joined.startswith("git -C /wt/feature rev-parse"):
                    probes["n"] += 1
                    return _cp(128) if probes["n"] == 1 else _cp(0)
                return _cp(0)

        got = run_with(Recovering(), bg._branch_worktree_path_live, REPO, "feature")
        self.assertEqual(got, "/wt/feature")
        self.assertEqual(probes["n"], 2)

    def test_still_stale_after_prune_gives_up(self):
        # prune 之后 list 还报它，但目录依旧探不通 → 当成没占用
        fake = FakeRun({"git worktree list": _cp(0, stdout=self.LIST),
                        "git -C /wt/feature rev-parse": _cp(128)})
        self.assertEqual(run_with(fake, bg._branch_worktree_path_live, REPO, "feature"), "")


class TestSingleRepoCmd(unittest.TestCase):
    """_single_repo_cmd：有专用入口就用，没有才回退通用入口 + 分支名。"""

    def test_dedicated_entry_wins(self):
        self.assertEqual(bg._single_repo_cmd("push", "canary"), ["push_canary"])

    def test_falls_back_to_branch_entry(self):
        self.assertEqual(bg._single_repo_cmd("push", "feature/x"),
                         ["push_branch", "feature/x"])


class TestMergeTreeConflicts(unittest.TestCase):
    """merge-tree 预演：0=干净 1=冲突，其余退出码表示这个 git 不支持。"""

    def test_clean(self):
        fake = FakeRun({"git merge-tree": _cp(0)})
        self.assertIs(run_with(fake, bg._merge_tree_conflicts, REPO, "HEAD", "origin/x"), False)

    def test_conflict(self):
        fake = FakeRun({"git merge-tree": _cp(1, stdout="a.txt")})
        self.assertIs(run_with(fake, bg._merge_tree_conflicts, REPO, "HEAD", "origin/x"), True)

    def test_old_git_returns_none(self):
        fake = FakeRun({"git merge-tree": _cp(129, stderr="unknown option")})
        self.assertIsNone(run_with(fake, bg._merge_tree_conflicts, REPO, "HEAD", "origin/x"))


class TestPushHeal(unittest.TestCase):
    """_push_heal：被拒后 re-fetch 判形，只有「远端有新提交」才值得再来一轮。"""

    def heal(self, fake: FakeRun, **kw):
        return run_with(fake, bg._push_heal, REPO, "canary", _r(), **kw)

    def test_success_returns_none(self):
        self.assertIsNone(self.heal(FakeRun()))

    def test_force_uses_force_with_lease(self):
        fake = FakeRun()
        self.heal(fake, force=True)
        self.assertTrue(fake.ran("git push --force-with-lease origin canary"))

    def test_single_round_failure_is_final(self):
        fake = FakeRun({"git push": _cp(1, stderr="rejected")})
        self.assertEqual(self.heal(fake, rounds=1),
                         ("fail", bg._extract_error("rejected", 1, "push canary")))

    def test_rejection_without_remote_progress_is_final(self):
        # 远端没前进（behind=0）→ 再推也一样，直接失败
        fake = FakeRun({"git push": _cp(1, stderr="rejected"),
                        "git rev-list": _cp(0, stdout="2\t0")})
        status, _ = self.heal(fake)
        self.assertEqual(status, "fail")
        self.assertEqual(sum(1 for c in fake.calls if c[:2] == ["git", "push"]), 1)

    def test_diverged_with_conflicts_skips(self):
        fake = FakeRun({"git push": _cp(1, stderr="rejected"),
                        "git rev-list": _cp(0, stdout="1\t2"),
                        "git merge-tree": _cp(1, stdout="a.txt")})
        self.assertEqual(self.heal(fake), ("skip", "分叉且合并预演有冲突（canary）"))

    def test_diverged_pull_failure_skips(self):
        fake = FakeRun({"git push": _cp(1, stderr="rejected"),
                        "git rev-list": _cp(0, stdout="1\t2"),
                        "git merge-tree": _cp(0),
                        "git pull": _cp(1, stderr="CONFLICT (content): a.txt")})
        status, detail = self.heal(fake)
        self.assertEqual(status, "skip")
        self.assertIn("CONFLICT", detail)

    def test_second_push_after_merge_succeeds(self):
        pushes = {"n": 0}

        class Flaky(FakeRun):
            def __call__(self, cmd, **kw):
                joined = " ".join(cmd)
                self.calls.append(list(cmd))
                if joined.startswith("git push"):
                    pushes["n"] += 1
                    return _cp(1, stderr="rejected") if pushes["n"] == 1 else _cp(0)
                if joined.startswith("git rev-list"):
                    return _cp(0, stdout="1\t2")
                return _cp(0)

        self.assertIsNone(self.heal(Flaky()))
        self.assertEqual(pushes["n"], 2)

    def test_gives_up_after_all_rounds(self):
        fake = FakeRun({"git push": _cp(1, stderr="rejected"),
                        "git rev-list": _cp(0, stdout="1\t2"),
                        "git merge-tree": _cp(0)})
        self.assertEqual(self.heal(fake, rounds=1)[0], "fail")


class TestPushFactoryDetect(unittest.TestCase):
    """_push_one_factory 的 detect：fetch 挂了就别往下走。"""

    def test_fetch_failure_fails_the_repo(self):
        fake = FakeRun({"git fetch": _cp(128, stderr="could not resolve host")})
        status, detail = exec_plan(fake, bg._push_one_factory("canary", False, False, []))
        self.assertEqual(status, "fail")
        self.assertIn("fetch origin 失败", detail)


class TestSyncExecute(unittest.TestCase):
    """_sync_one_factory 的 execute：merge_push 分支的两条失败路径。"""

    def _plan(self, action: str) -> RepoPlan:
        """先用一组「当前分支领先（可能还分叉）」的假输出跑 detect，拿到带 execute 的 plan。"""
        counts = "1\t1" if action == "merge_push" else "1\t0"
        rules = {"git branch --show-current": _cp(0, stdout="canary"),
                 "git rev-list": _cp(0, stdout=counts),
                 "git merge-tree": _cp(0)}
        plan = run_with(FakeRun(rules), bg._sync_one_factory(None, False), REPO, _r(), ROOT)
        assert plan.execute is not None and plan.detail.endswith(action), plan
        return plan

    def _execute(self, fake: FakeRun, action: str):
        plan = self._plan(action)
        return run_with(fake, plan.execute, REPO, plan, _r(), ROOT)

    def test_merge_push_pull_failure_skips(self):
        fake = FakeRun({"git branch --show-current": _cp(0, stdout="canary"),
                        "git pull": _cp(1, stderr="CONFLICT (content): a.txt")})
        status, detail = self._execute(fake, "merge_push")
        self.assertEqual(status, "skip")
        self.assertIn("CONFLICT", detail)

    def test_push_failure_propagates_from_heal(self):
        fake = FakeRun({"git branch --show-current": _cp(0, stdout="canary"),
                        "git push": _cp(1, stderr="rejected"),
                        "git rev-list": _cp(0, stdout="1\t0")})
        self.assertEqual(self._execute(fake, "push")[0], "fail")

    def test_merge_push_success_reports_the_sha(self):
        fake = FakeRun({"git branch --show-current": _cp(0, stdout="canary"),
                        "git rev-parse": _cp(0, stdout="abc1234\n")})
        status, detail = self._execute(fake, "merge_push")
        self.assertEqual(status, "ok")
        self.assertIn("合并分叉并推送 → origin/canary (abc1234)", detail)


class TestPushBranchExecuteMore(unittest.TestCase):
    """_push_branch_one_factory 的 execute：merge 模式、新建远端、结果文案。"""

    def _execute(self, fake: FakeRun, detail: str, *, force: bool = False):
        detect = bg._push_branch_one_factory("canary", force)
        probe = run_with(FakeRun({"git rev-list": _cp(0, stdout="1\t0")}), detect, REPO, _r(), ROOT)
        plan = RepoPlan(status="ok", detail=detail, execute=probe.execute)
        return run_with(fake, plan.execute, REPO, plan, _r(), ROOT)

    def test_merge_mode_pull_failure_skips(self):
        fake = FakeRun({"git branch --show-current": _cp(0, stdout="canary"),
                        "git pull --no-rebase": _cp(1, stderr="CONFLICT")})
        status, detail = self._execute(fake, "canary|1|2|merge")
        self.assertEqual(status, "skip")
        self.assertIn("自动 merge 失败", detail)

    def test_merge_mode_success_reports_merge(self):
        fake = FakeRun({"git branch --show-current": _cp(0, stdout="canary"),
                        "git rev-parse": _cp(0, stdout="abc1234\n")})
        status, detail = self._execute(fake, "canary|1|2|merge")
        self.assertEqual(status, "ok")
        self.assertIn("合并分叉并推送", detail)

    def test_new_remote_branch_push_failure(self):
        fake = FakeRun({"git branch --show-current": _cp(0, stdout="canary"),
                        "git push -u": _cp(1, stderr="denied")})
        status, detail = self._execute(fake, "canary|0|1|push")
        self.assertEqual(status, "fail")
        self.assertIn("push 失败", detail)

    def test_new_remote_branch_success(self):
        fake = FakeRun({"git branch --show-current": _cp(0, stdout="canary"),
                        "git rev-parse": _cp(0, stdout="abc1234\n")})
        status, detail = self._execute(fake, "canary|0|1|push")
        self.assertEqual(status, "ok")
        self.assertIn("新建远端分支", detail)


class TestDeleteBranchWorktree(unittest.TestCase):
    """_delete_branch_one_factory：被 worktree 占用时的四条分支。"""

    LIST = "worktree /wt/feature\nbranch refs/heads/feature\n"

    def detect(self, force: bool = False):
        return bg._delete_branch_one_factory("feature", force)

    def base_rules(self, **extra):
        rules = {"git worktree list": _cp(0, stdout=self.LIST),
                 "git -C /wt/feature rev-parse": _cp(0),
                 "git show-ref": _cp(0)}
        rules.update(extra)
        return FakeRun(rules)

    def test_dirty_worktree_is_skipped_at_detect(self):
        fake = self.base_rules(**{"git -C /wt/feature status": _cp(0, stdout=" M a.txt")})
        with mock.patch.object(bg, "_get_current_branch", return_value="main"):
            status, detail = exec_plan(fake, self.detect())
        self.assertEqual(status, "skip")
        self.assertIn("被 worktree 使用且有未提交改动", detail)

    def test_unreadable_worktree_fails_at_detect(self):
        fake = self.base_rules(**{"git -C /wt/feature status": _cp(128, stderr="gone")})
        with mock.patch.object(bg, "_get_current_branch", return_value="main"):
            status, detail = exec_plan(fake, self.detect())
        self.assertEqual(status, "fail")
        self.assertIn("检查 worktree 失败", detail)

    def test_clean_worktree_is_removed_then_branch_deleted(self):
        fake = self.base_rules()
        with mock.patch.object(bg, "_get_current_branch", return_value="main"):
            status, detail = exec_plan(fake, self.detect())
        self.assertEqual((status, detail), ("ok", "已删本地 feature"))
        self.assertTrue(fake.ran("git worktree remove /wt/feature"))

    def test_worktree_remove_failure_fails(self):
        fake = self.base_rules(**{"git worktree remove": _cp(1, stderr="locked")})
        with mock.patch.object(bg, "_get_current_branch", return_value="main"):
            status, detail = exec_plan(fake, self.detect())
        self.assertEqual(status, "fail")
        self.assertIn("删除 worktree 失败", detail)

    def test_execute_rechecks_a_worktree_that_turned_dirty(self):
        """detect 和 execute 之间有时间差：执行时再查一次 worktree 脏不脏。"""
        plan = RepoPlan(status="ok", detail="/wt/feature")
        with mock.patch.object(bg, "_get_current_branch", return_value="main"):
            probe = run_with(self.base_rules(), self.detect(), REPO, _r(), ROOT)
        fake = FakeRun({"git -C /wt/feature status": _cp(0, stdout=" M a.txt")})
        status, detail = run_with(fake, probe.execute, REPO, plan, _r(), ROOT)
        self.assertEqual(status, "skip")
        self.assertIn("被 worktree 使用且有未提交改动", detail)

    def test_execute_fails_when_the_worktree_became_unreadable(self):
        with mock.patch.object(bg, "_get_current_branch", return_value="main"):
            probe = run_with(self.base_rules(), self.detect(), REPO, _r(), ROOT)
        plan = RepoPlan(status="ok", detail="/wt/feature")
        fake = FakeRun({"git -C /wt/feature status": _cp(128, stderr="no such file")})
        status, detail = run_with(fake, probe.execute, REPO, plan, _r(), ROOT)
        self.assertEqual(status, "fail")
        self.assertIn("no such file", detail)  # 原始 git 报错照抄出来

    def test_unmerged_branch_is_skipped_without_force(self):
        fake = FakeRun({"git worktree list": _cp(0, stdout=""), "git show-ref": _cp(0),
                        "git branch -d": _cp(1, stderr="error: the branch 'feature' is not fully merged")})
        with mock.patch.object(bg, "_get_current_branch", return_value="main"):
            status, detail = exec_plan(fake, self.detect())
        self.assertEqual(status, "skip")
        self.assertIn("未合并", detail)

    def test_force_deletes_unmerged(self):
        fake = FakeRun({"git worktree list": _cp(0, stdout=""), "git show-ref": _cp(0)})
        with mock.patch.object(bg, "_get_current_branch", return_value="main"):
            status, _ = exec_plan(fake, self.detect(force=True))
        self.assertEqual(status, "ok")
        self.assertTrue(fake.ran("git branch -D feature"))

    def test_other_delete_failure_is_a_failure(self):
        fake = FakeRun({"git worktree list": _cp(0, stdout=""), "git show-ref": _cp(0),
                        "git branch -d": _cp(1, stderr="fatal: 说不清")})
        with mock.patch.object(bg, "_get_current_branch", return_value="main"):
            status, detail = exec_plan(fake, self.detect())
        self.assertEqual(status, "fail")
        self.assertIn("说不清", detail)


if __name__ == "__main__":
    unittest.main()

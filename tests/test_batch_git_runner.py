"""lib/batch_git.py 补充测试：调度器 + 各 factory 的 execute 段。

tests/test_batch_git.py 覆盖的是扫描、汇总打印和几个 detect 分支；这里补
BatchRunner.run 的完整流程（并发 detect、串行 execute、确认门、异常兜底）、
CallbackBatchOperation / run_single_repo 的契约、以及 merge / switch /
sync / push_branch / delete 各 factory 的 execute 段和 *_all 入口。

所有 git 调用都走假的 _run，不碰真仓库。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lib.batch_git as bg  # noqa: E402
from lib.batch_git import (  # noqa: E402
    BatchResult,
    BatchRunner,
    CallbackBatchOperation,
    RepoPlan,
    RepoResult,
    run_single_repo,
)


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRun:
    """按 git 子命令前缀匹配的假 _run。未命中的调用一律成功、无输出。"""

    def __init__(self, rules: dict[str, SimpleNamespace] | None = None) -> None:
        self.rules = rules or {}
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **_kwargs):
        self.calls.append(list(cmd))
        joined = " ".join(cmd)
        best = None
        for prefix, result in self.rules.items():
            if joined.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
                best = (prefix, result)
        return best[1] if best else _cp(0)

    def ran(self, prefix: str) -> bool:
        return any(" ".join(c).startswith(prefix) for c in self.calls)


def _r() -> mock.MagicMock:
    return mock.MagicMock()


def _plan_of(detect, repo=Path("/repo"), root=Path("/")) -> RepoPlan:
    return detect(repo, _r(), root)


def _exec_plan(detect, repo=Path("/repo"), root=Path("/")) -> tuple[str, str]:
    plan = detect(repo, _r(), root)
    assert plan.execute is not None, f"plan 没有 execute: {plan}"
    return plan.execute(repo, plan, _r(), root)


# ── 调度器 ────────────────────────────────────────────────────────────

class TestCallbackBatchOperation(unittest.TestCase):
    def _op(self, **kw) -> CallbackBatchOperation:
        return CallbackBatchOperation(title="t", root=Path("."), **kw)

    def test_post_init_fills_folder_name_and_script_dir(self) -> None:
        op = self._op()
        self.assertEqual(op.folder_name, Path(".").resolve().name)
        self.assertTrue(op.script_dir.exists())

    def test_explicit_folder_name_is_kept(self) -> None:
        op = CallbackBatchOperation(title="t", root=Path("."), folder_name="自定义")
        self.assertEqual(op.folder_name, "自定义")

    def test_missing_detect_fn_fails(self) -> None:
        plan = self._op().detect(Path("/repo"), _r(), Path("/"))
        self.assertEqual(plan.status, "fail")
        self.assertIn("detect_fn", plan.detail)

    def test_detect_returning_none_fails(self) -> None:
        op = self._op(detect_fn=lambda repo, r, root: None)
        plan = op.detect(Path("/repo"), _r(), Path("/"))
        self.assertEqual(plan.status, "fail")

    def test_execute_without_callable_maps_ok_to_skip(self) -> None:
        op = self._op()
        outcome = op.execute(Path("/repo"), RepoPlan(status="ok"), _r(), Path("/"))
        self.assertEqual((outcome.status, outcome.detail), ("skip", "无可执行操作"))

    def test_execute_without_callable_keeps_fail_status(self) -> None:
        op = self._op()
        outcome = op.execute(Path("/repo"), RepoPlan(status="fail", detail="炸了"), _r(), Path("/"))
        self.assertEqual((outcome.status, outcome.detail), ("fail", "炸了"))

    def test_execute_delegates_to_the_plan_callable(self) -> None:
        op = self._op()
        plan = RepoPlan(status="ok", execute=lambda *a: ("ok", "跑完了"))
        outcome = op.execute(Path("/repo"), plan, _r(), Path("/"))
        self.assertEqual((outcome.status, outcome.detail), ("ok", "跑完了"))

    def test_scan_delegates_to_scan_repos(self) -> None:
        op = self._op()
        with mock.patch.object(bg, "scan_repos", return_value=[Path("/a")]) as s:
            self.assertEqual(op.scan(), [Path("/a")])
        s.assert_called_once()

    def test_notify_adapter_forwards_to_notify_batch_done(self) -> None:
        op = self._op()
        with mock.patch.object(bg, "notify_batch_done") as n:
            op.notify("folder", BatchResult(total=0), script_dir=Path("."))
        n.assert_called_once()


class TestRunSingleRepo(unittest.TestCase):
    def test_detect_returning_none_fails(self) -> None:
        self.assertEqual(
            run_single_repo(lambda *a: None, Path("/repo"), _r(), Path("/")),
            ("fail", "detect 返回 None"),
        )

    def test_ok_without_execute_becomes_skip(self) -> None:
        got = run_single_repo(lambda *a: RepoPlan(status="ok"), Path("/repo"), _r(), Path("/"))
        self.assertEqual(got, ("skip", "无可执行操作"))

    def test_ok_without_execute_keeps_custom_detail(self) -> None:
        got = run_single_repo(lambda *a: RepoPlan(status="ok", detail="已最新"),
                              Path("/repo"), _r(), Path("/"))
        self.assertEqual(got, ("skip", "已最新"))

    def test_skip_plan_passes_through(self) -> None:
        got = run_single_repo(lambda *a: RepoPlan(status="skip", detail="无需处理"),
                              Path("/repo"), _r(), Path("/"))
        self.assertEqual(got, ("skip", "无需处理"))

    def test_execute_is_called(self) -> None:
        plan = RepoPlan(status="ok", execute=lambda *a: ("ok", "干完"))
        got = run_single_repo(lambda *a: plan, Path("/repo"), _r(), Path("/"))
        self.assertEqual(got, ("ok", "干完"))


class RunnerCase(unittest.TestCase):
    """跑 BatchRunner 时把 reporter / progress / 通知都换成假的。"""

    def setUp(self) -> None:
        self.reporter = mock.MagicMock()
        patches = [
            mock.patch.object(bg, "reporter", return_value=self.reporter),
            mock.patch.object(bg, "progress", return_value=None),
            mock.patch.object(bg, "notify_batch_done"),
            mock.patch.object(bg, "print_summary"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_with(self, detect_fn, repos: list[Path], *, confirm: bool = False,
                 root: Path = Path("/root")) -> BatchResult:
        op = CallbackBatchOperation(title="t", root=root, confirm=confirm, detect_fn=detect_fn)
        op.root = root  # 绕开 __post_init__ 的 resolve，测试里 root 是虚构路径
        with mock.patch.object(op, "scan", return_value=repos), \
             mock.patch.object(bg, "notify_batch_done"):
            return BatchRunner().run(op)


class TestBatchRunner(RunnerCase):
    def test_skip_plans_never_reach_execute(self) -> None:
        detect = mock.Mock(return_value=RepoPlan(status="skip", detail="没差异"))
        result = self.run_with(detect, [Path("/root/a"), Path("/root/b")])
        self.assertEqual(result.total, 2)
        self.assertEqual(len(result.skipped), 2)
        self.assertEqual(result.succeeded, [])

    def test_ok_plans_run_execute_serially(self) -> None:
        order: list[str] = []

        def ex(repo, plan, r, root):
            order.append(repo.name)
            return "ok", ""

        detect = lambda repo, r, root: RepoPlan(status="ok", execute=ex)  # noqa: E731
        result = self.run_with(detect, [Path("/root/a"), Path("/root/b")])
        self.assertEqual(order, ["a", "b"])
        self.assertEqual(len(result.succeeded), 2)

    def test_execute_result_can_be_skip_or_fail(self) -> None:
        def ex(repo, plan, r, root):
            return ("skip", "跳过") if repo.name == "a" else ("fail", "炸了")

        detect = lambda repo, r, root: RepoPlan(status="ok", execute=ex)  # noqa: E731
        result = self.run_with(detect, [Path("/root/a"), Path("/root/b")])
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(len(result.failed), 1)

    def test_detect_exception_becomes_a_failed_repo(self) -> None:
        def detect(repo, r, root):
            raise RuntimeError("检测炸了")

        result = self.run_with(detect, [Path("/root/a")])
        self.assertEqual(len(result.failed), 1)
        self.assertIn("检测炸了", result.failed[0].detail)

    def test_detect_returning_none_becomes_a_failed_repo(self) -> None:
        result = self.run_with(lambda *a: None, [Path("/root/a")])
        self.assertEqual(len(result.failed), 1)

    def test_execute_exception_becomes_a_failed_repo(self) -> None:
        def ex(repo, plan, r, root):
            raise RuntimeError("执行炸了")

        detect = lambda repo, r, root: RepoPlan(status="ok", execute=ex)  # noqa: E731
        result = self.run_with(detect, [Path("/root/a")])
        self.assertEqual(len(result.failed), 1)
        self.assertIn("执行炸了", result.failed[0].detail)

    def test_per_repo_detect_output_is_flushed_to_stderr(self) -> None:
        def detect(repo, r, root):
            r.info("检测日志")
            return RepoPlan(status="skip", detail="")

        buf = mock.Mock()
        with mock.patch.object(bg.sys, "stderr", buf):
            self.run_with(detect, [Path("/root/a")])
        self.assertTrue(buf.write.called)

    def test_empty_repo_list_still_summarises(self) -> None:
        result = self.run_with(lambda *a: RepoPlan(status="ok"), [])
        self.assertEqual(result.total, 0)

    def test_concurrency_reads_the_env_var(self) -> None:
        with mock.patch.dict(os.environ, {"BATCH_CONCURRENCY": "7"}):
            self.run_with(lambda *a: RepoPlan(status="skip"), [Path("/root/a")])
        printed = " ".join(str(c[0][0]) for c in self.reporter.info.call_args_list)
        self.assertIn("并发 7", printed)

    def test_confirm_yes_proceeds(self) -> None:
        with mock.patch.dict(os.environ, {"BATCH_NO_CONFIRM": ""}), \
             mock.patch.object(bg.sys.stdin, "isatty", return_value=True), \
             mock.patch("builtins.input", return_value="y"):
            result = self.run_with(lambda *a: RepoPlan(status="skip"), [Path("/root/a")],
                                   confirm=True)
        self.assertEqual(result.total, 1)

    def test_confirm_no_exits_zero(self) -> None:
        with mock.patch.dict(os.environ, {"BATCH_NO_CONFIRM": ""}), \
             mock.patch.object(bg.sys.stdin, "isatty", return_value=True), \
             mock.patch("builtins.input", return_value="n"):
            with self.assertRaises(SystemExit) as cm:
                self.run_with(lambda *a: RepoPlan(status="skip"), [Path("/root/a")], confirm=True)
        self.assertEqual(cm.exception.code, 0)

    def test_confirm_eof_is_treated_as_no(self) -> None:
        with mock.patch.dict(os.environ, {"BATCH_NO_CONFIRM": ""}), \
             mock.patch.object(bg.sys.stdin, "isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=EOFError):
            with self.assertRaises(SystemExit):
                self.run_with(lambda *a: RepoPlan(status="skip"), [Path("/root/a")], confirm=True)

    def test_confirm_without_tty_exits_one(self) -> None:
        with mock.patch.dict(os.environ, {"BATCH_NO_CONFIRM": ""}), \
             mock.patch.object(bg.sys.stdin, "isatty", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                self.run_with(lambda *a: RepoPlan(status="skip"), [Path("/root/a")], confirm=True)
        self.assertEqual(cm.exception.code, 1)

    def test_batch_no_confirm_env_skips_the_prompt(self) -> None:
        with mock.patch.dict(os.environ, {"BATCH_NO_CONFIRM": "1"}), \
             mock.patch("builtins.input", side_effect=AssertionError("不该问")):
            result = self.run_with(lambda *a: RepoPlan(status="skip"), [Path("/root/a")],
                                   confirm=True)
        self.assertEqual(result.total, 1)

    def test_progress_bar_is_driven_when_available(self) -> None:
        prog = mock.MagicMock()
        prog.add_task.return_value = 1
        detect = lambda repo, r, root: RepoPlan(status="ok", execute=lambda *a: ("ok", ""))  # noqa: E731
        with mock.patch.object(bg, "progress", return_value=prog), \
             mock.patch.object(bg, "print_ansi", return_value=True):
            self.run_with(detect, [Path("/root/a")])
        self.assertTrue(prog.start.called)
        self.assertTrue(prog.stop.called)
        self.assertTrue(prog.advance.called)

    def test_buffered_output_falls_back_when_print_ansi_fails(self) -> None:
        prog = mock.MagicMock()
        prog.add_task.return_value = 1

        def detect(repo, r, root):
            r.info("日志")
            return RepoPlan(status="skip")

        buf = mock.Mock()
        with mock.patch.object(bg, "progress", return_value=prog), \
             mock.patch.object(bg, "print_ansi", return_value=False), \
             mock.patch.object(bg.sys, "stderr", buf):
            self.run_with(detect, [Path("/root/a")])
        self.assertTrue(buf.write.called)


class TestRunBatchShim(unittest.TestCase):
    def test_run_batch_builds_a_callback_operation(self) -> None:
        with mock.patch.object(bg.BatchRunner, "run", return_value=BatchResult()) as run:
            bg.run_batch("标题", Path("."), lambda *a: RepoPlan(status="skip"),
                         folder_name="f", confirm=False)
        op = run.call_args[0][0]
        self.assertIsInstance(op, CallbackBatchOperation)
        self.assertEqual(op.title, "标题")
        self.assertEqual(op.folder_name, "f")
        self.assertFalse(op.confirm)


# ── merge factory ─────────────────────────────────────────────────────

class TestMergeFactory(unittest.TestCase):
    def test_fetch_failure_fails_the_repo(self) -> None:
        fake = FakeRun({"git fetch": _cp(1, stderr="fatal: 无法连接")})
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._merge_one_factory("canary", False, False, []))
        self.assertEqual(plan.status, "fail")
        self.assertIn("fetch origin 失败", plan.detail)

    def test_detached_head_is_skipped(self) -> None:
        with mock.patch.object(bg, "_run", FakeRun()), \
             mock.patch.object(bg, "_get_current_branch", return_value=""):
            plan = _plan_of(bg._merge_one_factory("canary", False, False, []))
        self.assertEqual(plan.status, "skip")
        self.assertIn("detached", plan.detail)

    def test_already_on_target_is_skipped(self) -> None:
        with mock.patch.object(bg, "_run", FakeRun()), \
             mock.patch.object(bg, "_get_current_branch", return_value="canary"):
            plan = _plan_of(bg._merge_one_factory("canary", False, False, []))
        self.assertEqual(plan.status, "skip")
        self.assertIn("已在 canary", plan.detail)

    def test_missing_remote_target_is_skipped(self) -> None:
        fake = FakeRun({"git show-ref": _cp(1)})
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"):
            plan = _plan_of(bg._merge_one_factory("canary", False, False, []))
        self.assertEqual(plan.status, "skip")
        self.assertIn("无远端 canary", plan.detail)

    def test_master_sentinel_resolves_the_real_main_branch(self) -> None:
        fake = FakeRun()
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"), \
             mock.patch.object(bg, "_resolve_main_branch", return_value="main") as res:
            plan = _plan_of(bg._merge_one_factory("master", False, False, []))
        res.assert_called_once()
        self.assertEqual(plan.status, "ok")
        self.assertTrue(fake.ran("git show-ref --verify --quiet refs/remotes/origin/main"))

    def test_dry_run_stops_before_execute(self) -> None:
        with mock.patch.object(bg, "_run", FakeRun()), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"):
            plan = _plan_of(bg._merge_one_factory("canary", True, False, []))
        self.assertEqual(plan.status, "skip")
        self.assertIsNone(plan.execute)

    def test_execute_runs_the_merge_subcommand(self) -> None:
        fake = FakeRun()
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"):
            status, detail = _exec_plan(bg._merge_one_factory("canary", False, False, ["--no-check"]))
        self.assertEqual(status, "ok")
        self.assertTrue(fake.ran("merge_canary --no-check"))

    def test_execute_reports_the_subcommand_exit_code(self) -> None:
        fake = FakeRun({"merge_canary": _cp(3)})
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"):
            status, detail = _exec_plan(bg._merge_one_factory("canary", False, False, []))
        self.assertEqual(status, "fail")
        self.assertIn("退出码 3", detail)

    def test_auto_commit_runs_before_the_merge(self) -> None:
        fake = FakeRun()
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"), \
             mock.patch("lib.commit_wf._has_changes", return_value=(True, "M x")), \
             mock.patch("lib.commit_wf.run_commit", return_value=0) as commit:
            status, _ = _exec_plan(bg._merge_one_factory("canary", False, True, []))
        self.assertEqual(status, "ok")
        commit.assert_called_once()

    def test_auto_commit_failure_stops_the_merge(self) -> None:
        fake = FakeRun()
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"), \
             mock.patch("lib.commit_wf._has_changes", return_value=(True, "M x")), \
             mock.patch("lib.commit_wf.run_commit", return_value=1):
            status, detail = _exec_plan(bg._merge_one_factory("canary", False, True, []))
        self.assertEqual(status, "fail")
        self.assertIn("自动提交失败", detail)
        self.assertFalse(fake.ran("merge_canary"))


class TestMergeAll(unittest.TestCase):
    def test_dry_run_flag_reaches_the_factory(self) -> None:
        captured = {}

        def spy(target, dry_run, auto_commit, extra):
            captured.update(target=target, dry_run=dry_run, auto_commit=auto_commit, extra=extra)
            return lambda *a: RepoPlan(status="skip")

        with mock.patch.object(bg, "_merge_one_factory", side_effect=spy), \
             mock.patch.object(bg.BatchRunner, "run", return_value=BatchResult()):
            rc = bg.merge_all("canary", ["merge_canary", "--dry-run", "--stay"])
        self.assertEqual(rc, 0)
        self.assertTrue(captured["dry_run"])
        self.assertEqual(captured["extra"], ["--stay"])

    def test_auto_commit_is_consumed_and_not_passed_through(self) -> None:
        captured = {}

        def spy(target, dry_run, auto_commit, extra):
            captured.update(auto_commit=auto_commit, extra=extra)
            return lambda *a: RepoPlan(status="skip")

        with mock.patch.object(bg, "_merge_one_factory", side_effect=spy), \
             mock.patch.object(bg.BatchRunner, "run", return_value=BatchResult()):
            bg.merge_all("canary", ["merge_canary", "--auto-commit"])
        self.assertTrue(captured["auto_commit"])
        self.assertNotIn("--auto-commit", captured["extra"])

    def test_failure_returns_one(self) -> None:
        failed = BatchResult(total=1, failed=[RepoResult("a", "/a", "fail")])
        with mock.patch.object(bg, "_merge_one_factory", return_value=lambda *a: RepoPlan("skip")), \
             mock.patch.object(bg.BatchRunner, "run", return_value=failed):
            self.assertEqual(bg.merge_all("canary", ["merge_canary"]), 1)


# ── switch factory ────────────────────────────────────────────────────

class TestSwitchFactoryExecute(unittest.TestCase):
    def _exec(self, mode: str, fake: FakeRun) -> tuple[str, str]:
        detect = bg._switch_one_factory("feat")
        plan = RepoPlan(status="ok", detail=mode)
        # execute 是 detect 的闭包内函数，借一次 detect 拿到它
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="other"):
            real = detect(Path("/repo"), _r(), Path("/"))
            plan.execute = real.execute
            return plan.execute(Path("/repo"), plan, _r(), Path("/"))

    def test_local_mode_switches(self) -> None:
        fake = FakeRun()
        status, detail = self._exec("local", fake)
        self.assertEqual(status, "ok")
        self.assertTrue(fake.ran("git switch feat"))

    def test_local_mode_failure(self) -> None:
        status, detail = self._exec("local", FakeRun({"git switch feat": _cp(1)}))
        self.assertEqual(status, "fail")

    def test_remote_mode_tracks_the_remote_branch(self) -> None:
        fake = FakeRun()
        status, _ = self._exec("remote", fake)
        self.assertEqual(status, "ok")
        self.assertTrue(fake.ran("git switch -c feat origin/feat"))

    def test_remote_mode_failure(self) -> None:
        status, _ = self._exec("remote", FakeRun({"git switch -c": _cp(1)}))
        self.assertEqual(status, "fail")

    def test_sync_behind_mode_fast_forwards(self) -> None:
        fake = FakeRun()
        status, detail = self._exec("sync-behind", fake)
        self.assertEqual(status, "ok")
        self.assertIn("快进对齐", detail)
        self.assertTrue(fake.ran("git pull --ff-only"))

    def test_sync_behind_failure_reports_the_error_line(self) -> None:
        fake = FakeRun({"git pull --ff-only": _cp(1, stderr="fatal: 分叉了")})
        status, detail = self._exec("sync-behind", fake)
        self.assertEqual(status, "fail")
        self.assertIn("分叉了", detail)

    def test_create_mode_branches_from_the_main_branch(self) -> None:
        fake = FakeRun()
        with mock.patch.object(bg, "_resolve_main_branch", return_value="main"):
            status, detail = self._exec("create", fake)
        self.assertEqual(status, "ok")
        self.assertIn("origin/main", detail)

    def test_create_mode_failure(self) -> None:
        fake = FakeRun({"git switch -c": _cp(1)})
        with mock.patch.object(bg, "_resolve_main_branch", return_value="main"):
            status, _ = self._exec("create", fake)
        self.assertEqual(status, "fail")


class TestSwitchFactoryDetect(unittest.TestCase):
    def test_fetch_failure_only_warns(self) -> None:
        fake = FakeRun({"git fetch": _cp(1, stdout="超时")})
        r = _r()
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="other"):
            plan = bg._switch_one_factory("feat")(Path("/repo"), r, Path("/"))
        r.warn.assert_called_once()
        self.assertEqual(plan.status, "ok")

    def test_already_on_target_and_behind_gets_sync_behind(self) -> None:
        fake = FakeRun({"git rev-list": _cp(0, "0\t3\n")})
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"):
            plan = bg._switch_one_factory("feat")(Path("/repo"), _r(), Path("/"))
        self.assertEqual((plan.status, plan.detail), ("ok", "sync-behind"))

    def test_already_on_target_and_ahead_is_skipped(self) -> None:
        fake = FakeRun({"git rev-list": _cp(0, "2\t0\n")})
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"):
            plan = bg._switch_one_factory("feat")(Path("/repo"), _r(), Path("/"))
        self.assertEqual(plan.status, "skip")
        self.assertIn("领先 2", plan.detail)

    def test_already_on_target_and_in_sync_is_skipped(self) -> None:
        fake = FakeRun({"git rev-list": _cp(0, "0\t0\n")})
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"):
            plan = bg._switch_one_factory("feat")(Path("/repo"), _r(), Path("/"))
        self.assertEqual(plan.detail, "已在目标分支")

    def test_dirty_worktree_fails(self) -> None:
        fake = FakeRun({
            "git diff --quiet": _cp(1),
            "git status --porcelain": _cp(0, " M a.txt\n"),
        })
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="other"):
            plan = bg._switch_one_factory("feat")(Path("/repo"), _r(), Path("/"))
        self.assertEqual(plan.status, "fail")
        self.assertIn("未提交改动", plan.detail)

    def test_remote_only_branch_gets_remote_mode(self) -> None:
        fake = FakeRun({"git show-ref --verify --quiet refs/heads/feat": _cp(1)})
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="other"):
            plan = bg._switch_one_factory("feat")(Path("/repo"), _r(), Path("/"))
        self.assertEqual(plan.detail, "remote")

    def test_unknown_branch_gets_create_mode(self) -> None:
        fake = FakeRun({"git show-ref": _cp(1)})
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="other"):
            plan = bg._switch_one_factory("feat")(Path("/repo"), _r(), Path("/"))
        self.assertEqual(plan.detail, "create")


class TestSwitchBranchAll(unittest.TestCase):
    def test_returns_zero_when_nothing_failed(self) -> None:
        with mock.patch.object(bg.BatchRunner, "run", return_value=BatchResult()):
            self.assertEqual(bg.switch_branch_all("feat"), 0)

    def test_returns_one_on_failure(self) -> None:
        failed = BatchResult(total=1, failed=[RepoResult("a", "/a", "fail")])
        with mock.patch.object(bg.BatchRunner, "run", return_value=failed):
            self.assertEqual(bg.switch_branch_all("feat"), 1)


# ── _resolve_main_branch ──────────────────────────────────────────────

class TestResolveMainBranch(unittest.TestCase):
    def test_reads_origin_head(self) -> None:
        fake = FakeRun({"git symbolic-ref": _cp(0, "origin/main\n")})
        with mock.patch.object(bg, "_run", fake):
            self.assertEqual(bg._resolve_main_branch(Path("/repo")), "main")

    def test_head_without_slash(self) -> None:
        fake = FakeRun({"git symbolic-ref": _cp(0, "trunk\n")})
        with mock.patch.object(bg, "_run", fake):
            self.assertEqual(bg._resolve_main_branch(Path("/repo")), "trunk")

    def test_sets_head_automatically_then_rereads(self) -> None:
        results = [_cp(1), _cp(0, "origin/main\n")]
        fake = FakeRun()

        def run(cmd, **kw):
            fake.calls.append(list(cmd))
            if cmd[:2] == ["git", "symbolic-ref"]:
                return results.pop(0)
            return _cp(0)

        with mock.patch.object(bg, "_run", run):
            self.assertEqual(bg._resolve_main_branch(Path("/repo")), "main")
        self.assertTrue(fake.ran("git remote set-head"))

    def test_enumerates_main_then_master(self) -> None:
        fake = FakeRun({
            "git symbolic-ref": _cp(1),
            "git show-ref --verify --quiet refs/remotes/origin/main": _cp(1),
        })
        with mock.patch.object(bg, "_run", fake):
            self.assertEqual(bg._resolve_main_branch(Path("/repo")), "master")

    def test_falls_back_to_master(self) -> None:
        fake = FakeRun({"git symbolic-ref": _cp(1), "git show-ref": _cp(1)})
        with mock.patch.object(bg, "_run", fake):
            self.assertEqual(bg._resolve_main_branch(Path("/repo")), "master")

    def test_empty_head_output_is_ignored(self) -> None:
        fake = FakeRun({"git symbolic-ref": _cp(0, "  \n"), "git show-ref": _cp(1)})
        with mock.patch.object(bg, "_run", fake):
            self.assertEqual(bg._resolve_main_branch(Path("/repo")), "master")


# ── sync factory ──────────────────────────────────────────────────────

class TestSyncFactoryExecute(unittest.TestCase):
    def _exec(self, detail: str, fake: FakeRun) -> tuple[str, str]:
        detect = bg._sync_one_factory("main", False)
        with mock.patch.object(bg, "_run", fake):
            probe = detect(Path("/repo"), _r(), Path("/"))
            plan = RepoPlan(status="ok", detail=detail, execute=probe.execute)
            return plan.execute(Path("/repo"), plan, _r(), Path("/"))

    def _base_rules(self) -> dict:
        return {"git rev-list": _cp(0, "0\t0\n"), "git rev-parse --short": _cp(0, "abc1234\n")}

    def test_checkout_happens_when_on_another_branch(self) -> None:
        fake = FakeRun({**self._base_rules(), "git branch --show-current": _cp(0, "feat\n")})
        status, detail = self._exec("main|origin/main|0|0", fake)
        self.assertEqual(status, "ok")
        self.assertTrue(fake.ran("git checkout -q main"))
        self.assertIn("已在最新", detail)

    def test_checkout_failure_stops_before_reset(self) -> None:
        fake = FakeRun({
            **self._base_rules(),
            "git branch --show-current": _cp(0, "feat\n"),
            "git checkout": _cp(1, stderr="error: 本地改动会被覆盖"),
        })
        status, detail = self._exec("main|origin/main|0|0", fake)
        self.assertEqual(status, "fail")
        self.assertIn("覆盖", detail)
        self.assertFalse(fake.ran("git reset"))

    def test_ahead_reports_discarded_commits(self) -> None:
        fake = FakeRun({**self._base_rules(), "git branch --show-current": _cp(0, "main\n")})
        status, detail = self._exec("main|origin/main|2|0", fake)
        self.assertIn("丢弃 2", detail)

    def test_behind_reports_fast_forward(self) -> None:
        fake = FakeRun({**self._base_rules(), "git branch --show-current": _cp(0, "main\n")})
        status, detail = self._exec("main|origin/main|0|3", fake)
        self.assertIn("快进 3", detail)


class TestSyncFactoryDetect(unittest.TestCase):
    def test_fetch_failure_fails(self) -> None:
        fake = FakeRun({"git fetch": _cp(1, stderr="fatal: 连不上")})
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._sync_one_factory("main", False))
        self.assertEqual(plan.status, "fail")

    def test_detached_head_is_skipped_when_branch_is_none(self) -> None:
        fake = FakeRun({"git branch --show-current": _cp(0, "\n")})
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._sync_one_factory(None, False))
        self.assertEqual(plan.status, "skip")
        self.assertIn("detached", plan.detail)

    def test_missing_remote_ref_is_skipped(self) -> None:
        fake = FakeRun({"git rev-parse --verify -q origin/main": _cp(1)})
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._sync_one_factory("main", False))
        self.assertEqual(plan.status, "skip")
        self.assertIn("无 origin/main", plan.detail)

    def test_missing_local_branch_is_created(self) -> None:
        fake = FakeRun({
            "git rev-parse --verify -q main": _cp(1),
            "git rev-list": _cp(0, "0\t0\n"),
        })
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._sync_one_factory("main", False))
        self.assertEqual(plan.status, "ok")
        self.assertTrue(fake.ran("git switch -c main origin/main"))

    def test_local_branch_creation_failure_fails(self) -> None:
        fake = FakeRun({
            "git rev-parse --verify -q main": _cp(1),
            "git switch -c": _cp(1, stderr="fatal: 创建失败"),
        })
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._sync_one_factory("main", False))
        self.assertEqual(plan.status, "fail")

    def test_dirty_worktree_fails(self) -> None:
        fake = FakeRun({
            "git diff-index": _cp(1),
            "git status --porcelain": _cp(0, " M a\n M b\n M c\n M d\n"),
        })
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._sync_one_factory("main", False))
        self.assertEqual(plan.status, "fail")
        self.assertIn("+1", plan.detail)

    def test_ahead_without_force_is_skipped_with_a_commit_list(self) -> None:
        fake = FakeRun({
            "git rev-list": _cp(0, "2\t0\n"),
            "git log": _cp(0, "abc1 feat: a\ndef2 fix: b\n"),
        })
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._sync_one_factory("main", False))
        self.assertEqual(plan.status, "skip")
        self.assertIn("领先 2 个 commit", plan.detail)
        self.assertIn("abc1", plan.detail)

    def test_ahead_with_force_proceeds(self) -> None:
        fake = FakeRun({"git rev-list": _cp(0, "2\t0\n")})
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._sync_one_factory("main", True))
        self.assertEqual(plan.status, "ok")
        self.assertEqual(plan.detail, "main|origin/main|2|0")

    def test_master_sentinel_resolves_the_real_branch(self) -> None:
        fake = FakeRun({"git rev-list": _cp(0, "0\t0\n")})
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_resolve_main_branch", return_value="trunk"):
            plan = _plan_of(bg._sync_one_factory(bg._MAIN_SENTINEL, False))
        self.assertTrue(plan.detail.startswith("trunk|origin/trunk"))

    def test_malformed_rev_list_output_defaults_to_zero(self) -> None:
        fake = FakeRun({"git rev-list": _cp(0, "")})
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._sync_one_factory("main", False))
        self.assertEqual(plan.detail, "main|origin/main|0|0")


class TestSyncEntryPoints(unittest.TestCase):
    def test_sync_branch_all_titles(self) -> None:
        with mock.patch.object(bg.BatchRunner, "run", return_value=BatchResult()) as run:
            bg.sync_branch_all()
            bg.sync_branch_all("dev")
        self.assertIn("当前分支", run.call_args_list[0][0][0].title)
        self.assertIn("dev", run.call_args_list[1][0][0].title)

    def test_sync_master_all_uses_the_sentinel(self) -> None:
        with mock.patch.object(bg, "sync_branch_all", return_value=0) as s:
            bg.sync_master_all(force=True)
        s.assert_called_once_with(bg._MAIN_SENTINEL, force=True)

    def test_failure_returns_one(self) -> None:
        failed = BatchResult(total=1, failed=[RepoResult("a", "/a", "fail")])
        with mock.patch.object(bg.BatchRunner, "run", return_value=failed):
            self.assertEqual(bg.sync_branch_all("dev"), 1)


# ── push_branch factory ───────────────────────────────────────────────

class TestPushBranchExecute(unittest.TestCase):
    def _exec(self, detail: str, fake: FakeRun, *, single: bool = False,
              force: bool = False) -> tuple[str, str]:
        detect = bg._push_branch_one_factory("feat", force, single=single)
        with mock.patch.object(bg, "_run", fake):
            probe = detect(Path("/repo"), _r(), Path("/"))
            plan = RepoPlan(status="ok", detail=detail, execute=probe.execute)
            return plan.execute(Path("/repo"), plan, _r(), Path("/"))

    def _base(self) -> dict:
        return {"git rev-parse --short HEAD": _cp(0, "abc1234\n")}

    def test_create_mode_branches_then_pushes_with_u(self) -> None:
        fake = FakeRun(self._base())
        with mock.patch.object(bg, "_resolve_main_branch", return_value="main"):
            status, detail = self._exec("feat|0|0|create", fake)
        self.assertEqual(status, "ok")
        self.assertTrue(fake.ran("git switch -c feat origin/main"))
        self.assertTrue(fake.ran("git push -u origin feat"))
        self.assertIn("新建远端分支", detail)

    def test_create_failure(self) -> None:
        fake = FakeRun({**self._base(), "git switch -c": _cp(1)})
        with mock.patch.object(bg, "_resolve_main_branch", return_value="main"):
            status, _ = self._exec("feat|0|0|create", fake)
        self.assertEqual(status, "fail")

    def test_checkout_failure(self) -> None:
        fake = FakeRun({
            **self._base(),
            "git branch --show-current": _cp(0, "other\n"),
            "git checkout": _cp(1, stderr="error: 切不过去"),
        })
        status, detail = self._exec("feat|1|1|push", fake)
        self.assertEqual(status, "fail")
        self.assertIn("切不过去", detail)

    def test_ff_only_pull_then_push(self) -> None:
        fake = FakeRun({**self._base(), "git branch --show-current": _cp(0, "feat\n")})
        status, detail = self._exec("feat|1|2|push", fake)
        self.assertEqual(status, "ok")
        self.assertTrue(fake.ran("git pull --ff-only"))
        self.assertIn("推送 2 个 commit", detail)

    def test_no_new_commits_message(self) -> None:
        fake = FakeRun({**self._base(), "git branch --show-current": _cp(0, "feat\n")})
        status, detail = self._exec("feat|1|0|push", fake)
        self.assertIn("无变化", detail)

    def test_batch_mode_skips_on_divergence(self) -> None:
        fake = FakeRun({
            **self._base(),
            "git branch --show-current": _cp(0, "feat\n"),
            "git pull --ff-only": _cp(1),
        })
        status, detail = self._exec("feat|1|0|push", fake, single=False)
        self.assertEqual(status, "skip")
        self.assertIn("分叉", detail)

    def test_single_mode_falls_back_to_a_merge_pull(self) -> None:
        fake = FakeRun({
            **self._base(),
            "git branch --show-current": _cp(0, "feat\n"),
            "git pull --ff-only": _cp(1),
        })
        status, _ = self._exec("feat|1|0|push", fake, single=True)
        self.assertEqual(status, "ok")
        self.assertTrue(fake.ran("git pull --no-rebase"))

    def test_single_mode_merge_failure_skips(self) -> None:
        fake = FakeRun({
            **self._base(),
            "git branch --show-current": _cp(0, "feat\n"),
            "git pull --ff-only": _cp(1),
            "git pull --no-rebase": _cp(1),
        })
        status, detail = self._exec("feat|1|0|push", fake, single=True)
        self.assertEqual(status, "skip")
        self.assertIn("手动解决冲突", detail)

    def test_force_uses_force_with_lease(self) -> None:
        fake = FakeRun({**self._base(), "git branch --show-current": _cp(0, "feat\n")})
        self._exec("feat|1|1|push", fake, force=True)
        self.assertTrue(fake.ran("git push --force-with-lease origin feat"))

    def test_push_failure(self) -> None:
        fake = FakeRun({
            **self._base(),
            "git branch --show-current": _cp(0, "feat\n"),
            "git push": _cp(1),
        })
        status, detail = self._exec("feat|1|1|push", fake)
        self.assertEqual(status, "fail")
        self.assertIn("push 失败", detail)


class TestPushBranchDetect(unittest.TestCase):
    def test_fetch_failure_fails(self) -> None:
        fake = FakeRun({"git fetch": _cp(1, stderr="fatal: 连不上")})
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._push_branch_one_factory("feat", False))
        self.assertEqual(plan.status, "fail")

    def test_detached_head_is_skipped(self) -> None:
        fake = FakeRun({"git branch --show-current": _cp(0, "\n")})
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._push_branch_one_factory(None, False))
        self.assertEqual(plan.status, "skip")

    def test_master_sentinel_resolves(self) -> None:
        fake = FakeRun({"git rev-list --count": _cp(0, "1\n")})
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_resolve_main_branch", return_value="main"):
            plan = _plan_of(bg._push_branch_one_factory(bg._MAIN_SENTINEL, False))
        self.assertTrue(plan.detail.startswith("main|1|1|push"))

    def test_dirty_worktree_fails(self) -> None:
        fake = FakeRun({
            "git diff-index": _cp(1),
            "git status --porcelain": _cp(0, " M a\n"),
        })
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._push_branch_one_factory("feat", False))
        self.assertEqual(plan.status, "fail")

    def test_missing_local_branch_with_remote_is_skipped(self) -> None:
        fake = FakeRun({"git rev-parse --verify -q feat": _cp(1)})
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._push_branch_one_factory("feat", False))
        self.assertEqual(plan.status, "skip")
        self.assertIn("无本地 feat", plan.detail)

    def test_missing_both_sides_gets_create_mode(self) -> None:
        fake = FakeRun({"git rev-parse --verify -q": _cp(1)})
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._push_branch_one_factory("feat", False))
        self.assertEqual(plan.detail, "feat|0|0|create")

    def test_ahead_count_is_encoded(self) -> None:
        fake = FakeRun({"git rev-list --count": _cp(0, "5\n")})
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._push_branch_one_factory("feat", False))
        self.assertEqual(plan.detail, "feat|1|5|push")


class TestPushBranchAll(unittest.TestCase):
    def test_single_repo_enables_single_mode(self) -> None:
        captured = {}

        def spy(branch, force, single=False):
            captured.update(branch=branch, force=force, single=single)
            return lambda *a: RepoPlan(status="skip")

        with mock.patch.object(bg, "scan_repos", return_value=[Path("/a")]), \
             mock.patch.object(bg, "_push_branch_one_factory", side_effect=spy), \
             mock.patch.object(bg.BatchRunner, "run", return_value=BatchResult()):
            bg.push_branch_all("feat", force=True)
        self.assertTrue(captured["single"])
        self.assertTrue(captured["force"])

    def test_multiple_repos_disable_single_mode(self) -> None:
        captured = {}

        def spy(branch, force, single=False):
            captured.update(single=single)
            return lambda *a: RepoPlan(status="skip")

        with mock.patch.object(bg, "scan_repos", return_value=[Path("/a"), Path("/b")]), \
             mock.patch.object(bg, "_push_branch_one_factory", side_effect=spy), \
             mock.patch.object(bg.BatchRunner, "run", return_value=BatchResult()):
            bg.push_branch_all(None)
        self.assertFalse(captured["single"])

    def test_titles_differ_by_branch(self) -> None:
        with mock.patch.object(bg, "scan_repos", return_value=[]), \
             mock.patch.object(bg.BatchRunner, "run", return_value=BatchResult()) as run:
            bg.push_branch_all()
            bg.push_branch_all("dev")
        self.assertIn("当前分支", run.call_args_list[0][0][0].title)
        self.assertIn("dev", run.call_args_list[1][0][0].title)

    def test_failure_returns_one(self) -> None:
        failed = BatchResult(total=1, failed=[RepoResult("a", "/a", "fail")])
        with mock.patch.object(bg, "scan_repos", return_value=[]), \
             mock.patch.object(bg.BatchRunner, "run", return_value=failed):
            self.assertEqual(bg.push_branch_all("dev"), 1)


# ── delete factories ──────────────────────────────────────────────────

class TestDeleteRemoteFactory(unittest.TestCase):
    def test_missing_remote_branch_is_skipped(self) -> None:
        fake = FakeRun({"git show-ref": _cp(1)})
        with mock.patch.object(bg, "_run", fake):
            plan = _plan_of(bg._delete_branch_remote_one_factory("feat", "origin"))
        self.assertEqual(plan.status, "skip")

    def test_delete_then_prune(self) -> None:
        fake = FakeRun()
        with mock.patch.object(bg, "_run", fake):
            status, detail = _exec_plan(bg._delete_branch_remote_one_factory("feat", "origin"))
        self.assertEqual(status, "ok")
        self.assertTrue(fake.ran("git push origin --delete feat"))
        self.assertTrue(fake.ran("git fetch --prune origin"))

    def test_delete_failure(self) -> None:
        fake = FakeRun({"git push origin --delete": _cp(1)})
        with mock.patch.object(bg, "_run", fake):
            status, detail = _exec_plan(bg._delete_branch_remote_one_factory("feat", "origin"))
        self.assertEqual(status, "fail")


class TestDeleteEntryPoints(unittest.TestCase):
    def test_delete_branch_all_asks_for_confirmation(self) -> None:
        with mock.patch.object(bg.BatchRunner, "run", return_value=BatchResult()) as run:
            bg.delete_branch_all("feat", force=True)
        op = run.call_args[0][0]
        self.assertTrue(op.confirm)
        self.assertIn("强删", op.title)

    def test_delete_branch_remote_all_asks_for_confirmation(self) -> None:
        with mock.patch.object(bg.BatchRunner, "run", return_value=BatchResult()) as run:
            bg.delete_branch_remote_all("feat", remote="upstream")
        op = run.call_args[0][0]
        self.assertTrue(op.confirm)
        self.assertIn("upstream/feat", op.title)

    def test_failures_return_one(self) -> None:
        failed = BatchResult(total=1, failed=[RepoResult("a", "/a", "fail")])
        with mock.patch.object(bg.BatchRunner, "run", return_value=failed):
            self.assertEqual(bg.delete_branch_all("feat"), 1)
            self.assertEqual(bg.delete_branch_remote_all("feat"), 1)


# ── push factory 补充分支 ─────────────────────────────────────────────

class TestPushFactoryExtra(unittest.TestCase):
    def test_missing_remote_target_passes_condition_one(self) -> None:
        fake = FakeRun({"git show-ref --verify --quiet refs/remotes/origin/canary": _cp(1),
                        "git show-ref --verify --quiet refs/heads/canary": _cp(0)})
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"):
            plan = _plan_of(bg._push_one_factory("canary", False, False, []))
        self.assertEqual(plan.status, "ok")

    def test_dirty_worktree_with_auto_commit_passes_condition_one(self) -> None:
        fake = FakeRun({
            "git log origin/canary..HEAD": _cp(0, ""),
            "git status --porcelain": _cp(0, " M a.txt\n"),
            "git log origin/canary..canary": _cp(0, ""),
        })
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"):
            plan = _plan_of(bg._push_one_factory("canary", False, True, []))
        self.assertEqual(plan.status, "ok")

    def test_local_target_ahead_passes_condition_two(self) -> None:
        fake = FakeRun({
            "git log origin/canary..HEAD": _cp(0, ""),
            "git status --porcelain": _cp(0, ""),
            "git log origin/canary..canary": _cp(0, "abc1 fix\n"),
        })
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"):
            plan = _plan_of(bg._push_one_factory("canary", False, False, []))
        self.assertEqual(plan.status, "ok")

    def test_no_local_target_is_reported_in_the_skip_reason(self) -> None:
        fake = FakeRun({
            "git log": _cp(0, ""),
            "git status --porcelain": _cp(0, ""),
            "git show-ref --verify --quiet refs/heads/canary": _cp(1),
        })
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"):
            plan = _plan_of(bg._push_one_factory("canary", False, False, []))
        self.assertEqual(plan.status, "skip")
        self.assertIn("无本地 canary 分支", plan.detail)

    def test_dry_run_stops_before_execute(self) -> None:
        fake = FakeRun({"git log origin/canary..HEAD": _cp(0, "abc1 feat\n")})
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"):
            plan = _plan_of(bg._push_one_factory("canary", True, False, []))
        self.assertEqual(plan.status, "skip")
        self.assertIn("dry-run", plan.detail)

    def test_execute_runs_the_push_subcommand(self) -> None:
        fake = FakeRun({"git log origin/canary..HEAD": _cp(0, "abc1 feat\n")})
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"):
            status, _ = _exec_plan(bg._push_one_factory("canary", False, False, ["--stay"]))
        self.assertEqual(status, "ok")
        self.assertTrue(fake.ran("push_canary --stay"))

    def test_execute_failure_reports_the_exit_code(self) -> None:
        fake = FakeRun({"git log origin/canary..HEAD": _cp(0, "abc1 feat\n"),
                        "push_canary": _cp(4)})
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"):
            status, detail = _exec_plan(bg._push_one_factory("canary", False, False, []))
        self.assertEqual(status, "fail")
        self.assertIn("退出码 4", detail)

    def test_auto_commit_failure_stops_the_push(self) -> None:
        fake = FakeRun({"git log origin/canary..HEAD": _cp(0, "abc1 feat\n")})
        with mock.patch.object(bg, "_run", fake), \
             mock.patch.object(bg, "_get_current_branch", return_value="feat"), \
             mock.patch("lib.commit_wf._has_changes", return_value=(True, "M x")), \
             mock.patch("lib.commit_wf.run_commit", return_value=1):
            status, detail = _exec_plan(bg._push_one_factory("canary", False, True, []))
        self.assertEqual(status, "fail")
        self.assertFalse(fake.ran("push_canary"))


class TestDirtyDetail(unittest.TestCase):
    def test_clean_worktree_gives_empty_detail(self) -> None:
        with mock.patch.object(bg, "_run", FakeRun({"git status": _cp(0, "")})):
            self.assertEqual(bg._dirty_detail(Path("/repo")), "")

    def test_three_files_are_listed_without_an_overflow_marker(self) -> None:
        out = " M a\n M b\n M c\n"
        with mock.patch.object(bg, "_run", FakeRun({"git status": _cp(0, out)})):
            detail = bg._dirty_detail(Path("/repo"))
        self.assertIn("3 项", detail)
        self.assertNotIn("+", detail)

    def test_overflow_marker_appears_past_three_files(self) -> None:
        out = "".join(f" M f{i}\n" for i in range(5))
        with mock.patch.object(bg, "_run", FakeRun({"git status": _cp(0, out)})):
            detail = bg._dirty_detail(Path("/repo"))
        self.assertIn("+2", detail)


if __name__ == "__main__":
    unittest.main()

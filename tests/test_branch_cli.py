"""delete_branch / delete_branch_remote CLI 层测试。

只验 CLI 行为：单仓 vs 批量的分派、参数校验、状态→退出码映射、`-y` 开关。
真正的 git 操作（`lib.batch_git`）一律 mock，不跑 git、不碰网络。
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.cli import delete_branch as db  # noqa: E402
from lib.cli import delete_branch_remote as dbr  # noqa: E402


def messages(reporter_mock, level: str) -> list[str]:
    return [call.args[0] for call in getattr(reporter_mock, level).call_args_list]


class EnvCase(unittest.TestCase):
    """保证 BATCH_NO_CONFIRM 不在用例之间泄漏。"""

    def setUp(self):
        p = mock.patch.dict(os.environ, {}, clear=False)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("BATCH_NO_CONFIRM", None)


class TestConsumeYes(EnvCase):
    """-y/--yes 由 argv 拦截器消费，转成 BATCH_NO_CONFIRM 环境变量。"""

    def test_without_flag_argv_unchanged(self):
        argv = ["delete_branch", "all", "feat"]
        self.assertEqual(db._consume_yes(argv), argv)
        self.assertNotIn("BATCH_NO_CONFIRM", os.environ)

    def test_short_flag_removed_and_env_set(self):
        self.assertEqual(db._consume_yes(["delete_branch", "all", "feat", "-y"]),
                         ["delete_branch", "all", "feat"])
        self.assertEqual(os.environ["BATCH_NO_CONFIRM"], "1")

    def test_long_flag_removed(self):
        self.assertEqual(db._consume_yes(["delete_branch", "--yes", "feat"]),
                         ["delete_branch", "feat"])
        self.assertEqual(os.environ["BATCH_NO_CONFIRM"], "1")

    def test_flag_in_argv0_is_not_consumed(self):
        # argv[0] 是程序名，只扫描 argv[1:]
        argv = ["-y", "feat"]
        self.assertEqual(db._consume_yes(argv), argv)
        self.assertNotIn("BATCH_NO_CONFIRM", os.environ)


class CwdMixin:
    """把 cwd 换成临时目录，按需造一个 .git 冒充 git 仓库。"""

    def fake_cwd(self, *, is_repo: bool):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        if is_repo:
            (root / ".git").mkdir()
        return mock.patch.object(pathlib.Path, "cwd", return_value=root)


class DeleteBranchCase(EnvCase, CwdMixin):
    def cli(self) -> db.DeleteBranchCli:
        c = db.DeleteBranchCli()
        c._r = mock.MagicMock()
        return c


class TestDeleteBranchDispatch(DeleteBranchCase):
    """裸调用按 cwd 是不是 git 仓库分派到 here / all。"""

    def test_no_branch_exits_1(self):
        c = self.cli()
        self.assertEqual(c(), 1)
        self.assertIn("delete_branch: 缺少分支名", messages(c._r, "err"))

    def test_inside_repo_routes_to_here(self):
        c = self.cli()
        with mock.patch.object(c, "here", return_value=0) as here, \
             mock.patch.object(c, "all") as all_, \
             self.fake_cwd(is_repo=True):
            self.assertEqual(c("feat", force=True), 0)
        here.assert_called_once_with("feat", force=True)
        all_.assert_not_called()

    def test_outside_repo_routes_to_all(self):
        c = self.cli()
        with mock.patch.object(c, "all", return_value=0) as all_, \
             mock.patch.object(c, "here") as here, \
             self.fake_cwd(is_repo=False):
            self.assertEqual(c("a", "b", yes=True), 0)
        all_.assert_called_once_with("a", "b", force=False, yes=True)
        here.assert_not_called()


class TestDeleteBranchHere(DeleteBranchCase):
    """here：逐个分支删，退出码按位或聚合。"""

    def test_no_branch_exits_1(self):
        c = self.cli()
        self.assertEqual(c.here(), 1)

    def test_each_branch_deleted_once(self):
        c = self.cli()
        with mock.patch.object(c, "_delete_one", return_value=0) as one:
            self.assertEqual(c.here("a", "b", force=True), 0)
        self.assertEqual([call.args for call in one.call_args_list],
                         [("a", True), ("b", True)])

    def test_one_failure_makes_whole_run_fail(self):
        c = self.cli()
        with mock.patch.object(c, "_delete_one", side_effect=[0, 1, 0]):
            self.assertEqual(c.here("a", "b", "c"), 1)


class TestDeleteBranchAll(DeleteBranchCase):
    """all：每个分支调一次批量删除；-y 写进环境变量。"""

    def test_no_branch_exits_1(self):
        self.assertEqual(self.cli().all(), 1)

    def test_calls_batch_per_branch(self):
        c = self.cli()
        with mock.patch.object(db, "delete_branch_all", return_value=0) as batch:
            self.assertEqual(c.all("a", "b", force=True), 0)
        self.assertEqual([call.args[0] for call in batch.call_args_list], ["a", "b"])
        self.assertEqual(batch.call_args.kwargs, {"force": True})
        self.assertNotIn("BATCH_NO_CONFIRM", os.environ)

    def test_yes_sets_no_confirm_env(self):
        c = self.cli()
        with mock.patch.object(db, "delete_branch_all", return_value=0):
            c.all("a", yes=True)
        self.assertEqual(os.environ["BATCH_NO_CONFIRM"], "1")

    def test_failure_propagates(self):
        c = self.cli()
        with mock.patch.object(db, "delete_branch_all", side_effect=[0, 1]):
            self.assertEqual(c.all("a", "b"), 1)


class TestDeleteBranchStatusMapping(DeleteBranchCase):
    """_delete_one：run_single_repo 的 status → 退出码 + 用哪个报告级别。"""

    def _run(self, status: str, detail: str = "详情"):
        c = self.cli()
        r = mock.MagicMock()
        with mock.patch.object(db, "reporter", return_value=r), \
             mock.patch.object(db, "_delete_branch_one_factory", return_value="OP") as factory, \
             mock.patch.object(db, "run_single_repo", return_value=(status, detail)) as run:
            rc = c._delete_one("feat", True)
        return rc, r, factory, run

    def test_ok_is_zero(self):
        rc, r, factory, run = self._run("ok")
        self.assertEqual(rc, 0)
        factory.assert_called_once_with("feat", True)
        self.assertEqual(run.call_args.args[0], "OP")
        r.ok.assert_called_once_with("详情")

    def test_skip_is_zero_and_warns(self):
        rc, r, _, _ = self._run("skip")
        self.assertEqual(rc, 0)
        r.warn.assert_called_once_with("详情")

    def test_fail_is_one_and_errs(self):
        rc, r, _, _ = self._run("fail")
        self.assertEqual(rc, 1)
        r.err.assert_called_once_with("详情")

    def test_unknown_status_is_one(self):
        rc, _, _, _ = self._run("???")
        self.assertEqual(rc, 1)

    def test_empty_detail_prints_nothing(self):
        rc, r, _, _ = self._run("ok", detail="")
        self.assertEqual(rc, 0)
        r.ok.assert_not_called()


class TestDeleteBranchMain(EnvCase):
    """main：先消费 -y，再交给 fire。"""

    def test_consumes_yes_before_fire(self):
        with mock.patch.object(sys, "argv", ["delete_branch", "all", "feat", "-y"]), \
             mock.patch.object(db, "run_cli") as run_cli:
            db.main()
            self.assertEqual(sys.argv, ["delete_branch", "all", "feat"])
        self.assertEqual(os.environ["BATCH_NO_CONFIRM"], "1")
        run_cli.assert_called_once()


class DeleteBranchRemoteCase(EnvCase, CwdMixin):
    def cli(self) -> dbr.DeleteBranchRemoteCli:
        c = dbr.DeleteBranchRemoteCli()
        c._r = mock.MagicMock()
        return c


class TestDeleteBranchRemoteDispatch(DeleteBranchRemoteCase):
    """裸调用只取第一个分支名，按 cwd 是不是 git 仓库分派。"""

    def test_no_branch_exits_1(self):
        c = self.cli()
        self.assertEqual(c(), 1)
        self.assertIn("delete_branch_remote: 缺少分支名", messages(c._r, "err"))

    def test_inside_repo_routes_to_here(self):
        c = self.cli()
        with mock.patch.object(c, "here", return_value=0) as here, \
             self.fake_cwd(is_repo=True):
            self.assertEqual(c("feat", remote="up"), 0)
        here.assert_called_once_with("feat", remote="up")

    def test_outside_repo_routes_to_all(self):
        c = self.cli()
        with mock.patch.object(c, "all", return_value=0) as all_, \
             self.fake_cwd(is_repo=False):
            self.assertEqual(c("feat", yes=True), 0)
        all_.assert_called_once_with("feat", remote="origin", yes=True)

    def test_every_branch_is_deleted_not_just_the_first(self):
        """和本地版 delete_branch 对齐：多给几个分支名就删几个，不能默默丢掉。"""
        c = self.cli()
        with mock.patch.object(c, "here", return_value=0) as here, \
             self.fake_cwd(is_repo=True):
            c("a", "b")
        here.assert_called_once_with("a", "b", remote="origin")

    def test_several_branches_reach_the_batch_path_too(self):
        c = self.cli()
        with mock.patch.object(c, "all", return_value=0) as batch, \
             self.fake_cwd(is_repo=False):
            c("a", "b", yes=True)
        batch.assert_called_once_with("a", "b", remote="origin", yes=True)


class TestDeleteBranchRemoteAll(DeleteBranchRemoteCase):
    """all：转发给批量实现；-y 写进环境变量。"""

    def test_forwards_remote(self):
        c = self.cli()
        with mock.patch.object(dbr, "delete_branch_remote_all", return_value=0) as batch:
            self.assertEqual(c.all("feat", remote="up"), 0)
        batch.assert_called_once_with("feat", remote="up")
        self.assertNotIn("BATCH_NO_CONFIRM", os.environ)

    def test_yes_sets_no_confirm_env(self):
        c = self.cli()
        with mock.patch.object(dbr, "delete_branch_remote_all", return_value=0):
            c.all("feat", yes=True)
        self.assertEqual(os.environ["BATCH_NO_CONFIRM"], "1")


class TestDeleteBranchRemoteHere(DeleteBranchRemoteCase):
    """here：容忍 origin/xxx 写法，状态映射同本地删除。"""

    def _run(self, branch: str, remote: str = "origin", status: str = "ok"):
        c = self.cli()
        r = mock.MagicMock()
        with mock.patch.object(dbr, "reporter", return_value=r), \
             mock.patch.object(dbr, "_delete_branch_remote_one_factory", return_value="OP") as factory, \
             mock.patch.object(dbr, "run_single_repo", return_value=(status, "详情")):
            rc = c.here(branch, remote=remote)
        return rc, r, factory

    def test_strips_remote_prefix(self):
        _, _, factory = self._run("origin/feature/x")
        factory.assert_called_once_with("feature/x", remote="origin")

    def test_strips_custom_remote_prefix(self):
        _, _, factory = self._run("up/feat", remote="up")
        factory.assert_called_once_with("feat", remote="up")

    def test_keeps_branch_named_like_other_remote(self):
        # remote 是 origin 时，up/feat 不该被剥
        _, _, factory = self._run("up/feat", remote="origin")
        factory.assert_called_once_with("up/feat", remote="origin")

    def test_fail_status_exits_1(self):
        rc, r, _ = self._run("feat", status="fail")
        self.assertEqual(rc, 1)
        r.err.assert_called_once_with("详情")

    def test_skip_status_exits_0(self):
        rc, r, _ = self._run("feat", status="skip")
        self.assertEqual(rc, 0)
        r.warn.assert_called_once_with("详情")


class TestDeleteBranchRemoteMain(unittest.TestCase):
    """main：直接交给 fire（远端删除没有 -y 拦截器）。"""

    def test_hands_off_to_fire(self):
        with mock.patch.object(dbr, "run_cli") as run_cli:
            dbr.main()
        run_cli.assert_called_once()
        self.assertIsInstance(run_cli.call_args.args[0], dbr.DeleteBranchRemoteCli)


if __name__ == "__main__":
    unittest.main()

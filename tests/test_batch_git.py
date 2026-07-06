#!/usr/bin/env python3
"""Tests for lib.batch_git."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.batch_git import (
    BatchResult,
    RepoResult,
    _push_one_factory,
    _switch_one_factory,
    _sync_one_factory,
    notify_batch_done,
    print_repo_list,
    print_summary,
    push_all,
    scan_gitlab_repos,
)
from lib.ui import reporter


def _mock_run(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestDataclasses(unittest.TestCase):
    def test_repo_result_defaults(self):
        r = RepoResult(name="a", path="/a", status="ok")
        self.assertEqual(r.detail, "")

    def test_batch_result_defaults(self):
        b = BatchResult(total=3)
        self.assertEqual(b.total, 3)
        self.assertEqual(b.succeeded, [])
        self.assertEqual(b.skipped, [])
        self.assertEqual(b.failed, [])


class TestScanGitlabRepos(unittest.TestCase):
    @patch("lib.batch_git.run")
    @patch("lib.batch_git.os.walk")
    def test_finds_gitlab_repos(self, mock_walk, mock_run):
        mock_walk.return_value = [
            ("/root", ["repo1"], []),
            ("/root/repo1", [".git"], []),
        ]
        mock_run.return_value = _mock_run(stdout="origin git@gitlab.com:g/p.git\n")
        repos = scan_gitlab_repos(Path("/root"))
        self.assertEqual(len(repos), 1)

    @patch("lib.batch_git.run")
    @patch("lib.batch_git.os.walk")
    def test_skips_non_gitlab(self, mock_walk, mock_run):
        mock_walk.return_value = [
            ("/root", ["repo1"], []),
            ("/root/repo1", [".git"], []),
        ]
        mock_run.return_value = _mock_run(stdout="origin git@github.com:o/r.git\n")
        repos = scan_gitlab_repos(Path("/root"))
        self.assertEqual(repos, [])

    @patch("lib.batch_git.run")
    @patch("lib.batch_git.os.walk")
    def test_max_depth_no_crash(self, mock_walk, _mock_run):
        # os.walk mock 无法复现 dirnames.clear() 语义, 仅验证不崩
        mock_walk.return_value = [("/root/a/b", [], [])]
        with patch("lib.batch_git.run", return_value=_mock_run(stdout="gitlab")):
            scan_gitlab_repos(Path("/root"), max_depth=1)

    @patch("lib.batch_git.run")
    @patch("lib.batch_git.os.walk")
    def test_removes_git_from_dirnames(self, mock_walk, _mock_run):
        # 同上, mock os.walk 无法验证 dirnames.remove; 仅验证入口不崩
        mock_walk.return_value = [("/root/repo", [], [])]
        with patch("lib.batch_git.run", return_value=_mock_run(stdout="gitlab")):
            scan_gitlab_repos(Path("/root"))


class TestPrintRepoList(unittest.TestCase):
    def test_renders(self):
        r = reporter(stderr=True)
        r.console = None
        # 不应崩
        print_repo_list(r, [Path("/root/repo1")], Path("/root"))


class TestPrintSummary(unittest.TestCase):
    def _capture(self):
        """捕获 Reporter 纯文本输出（强制 console=None 走 _eprint 路径）。"""
        import io
        from contextlib import redirect_stderr

        buf = io.StringIO()
        return buf, redirect_stderr(buf)

    def _make_reporter(self):
        r = reporter(stderr=True)
        r.console = None  # 走纯文本路径，输出可字符串断言
        return r

    def test_renders_all_statuses(self):
        r = self._make_reporter()
        result = BatchResult(total=3)
        result.succeeded.append(RepoResult("a", "/a", "ok"))
        result.skipped.append(RepoResult("b", "/b", "skip", "已对齐"))
        result.failed.append(RepoResult("c", "/c", "fail", "err"))
        buf, redir = self._capture()
        with redir:
            print_summary(r, "汇总", result)
        out = buf.getvalue()
        # 总计单行（含跳过数量，不显跳过明细）
        self.assertIn("总计 3 个（成功 1 / 跳过 1 / 失败 1）", out)
        # 成功段
        self.assertIn("成功项目：", out)
        self.assertIn("• a", out)
        # 失败段（含 detail）
        self.assertIn("失败项目：", out)
        self.assertIn("• c — err", out)
        # 跳过无明细
        self.assertNotIn("• b", out)

    def test_empty_sections_omitted(self):
        r = self._make_reporter()
        result = BatchResult(total=1)
        result.succeeded.append(RepoResult("only", "/o", "ok"))
        buf, redir = self._capture()
        with redir:
            print_summary(r, "汇总", result)
        out = buf.getvalue()
        self.assertIn("成功项目：", out)
        self.assertNotIn("失败项目：", out)
        self.assertIn("总计 1 个（成功 1 / 跳过 0 / 失败 0）", out)

    def test_failed_without_detail(self):
        r = self._make_reporter()
        result = BatchResult(total=1)
        result.failed.append(RepoResult("x", "/x", "fail"))  # detail 默认空
        buf, redir = self._capture()
        with redir:
            print_summary(r, "汇总", result)
        out = buf.getvalue()
        self.assertIn("• x", out)
        self.assertNotIn("—", out)


class TestNotifyBatchDone(unittest.TestCase):
    @patch("lib.notify.notify_via_n")
    def test_failed_message(self, mock_n):
        result = BatchResult(total=2)
        result.failed.append(RepoResult("x", "/x", "fail"))
        notify_batch_done("folder", result, script_dir=Path("/tmp"))
        mock_n.assert_called_once()
        msg = mock_n.call_args[0][0]
        self.assertIn("失败", msg)

    @patch("lib.notify.notify_via_n")
    def test_success_message(self, mock_n):
        result = BatchResult(total=2)
        result.succeeded.append(RepoResult("x", "/x", "ok"))
        notify_batch_done("folder", result, script_dir=Path("/tmp"))
        msg = mock_n.call_args[0][0]
        self.assertIn("成功", msg)

    @patch("lib.notify.notify_via_n")
    def test_empty_message(self, mock_n):
        result = BatchResult(total=0)
        notify_batch_done("folder", result, script_dir=Path("/tmp"))
        mock_n.assert_called_once()


class TestPushFactory(unittest.TestCase):
    def test_returns_callable(self):
        op = _push_one_factory(target="canary", dry_run=True, extra=[])
        self.assertTrue(callable(op))

    @patch("lib.batch_git._run")
    @patch("lib.batch_git._get_current_branch", return_value="feat")
    def test_dry_run_returns_ok(self, _mock_br, mock_run):
        # fetch ok, remote target 不存在 (cond1 通过), local target 不存在 (cond2 不通过)
        # cond1 || cond2 → 满足 → dry_run 返 ok
        mock_run.side_effect = [
            _mock_run(returncode=0),   # fetch
            _mock_run(returncode=1),   # show-ref remote target (不存在)
            _mock_run(returncode=1),   # show-ref local target (不存在)
        ]
        op = _push_one_factory(target="canary", dry_run=True, extra=[])
        r = MagicMock()
        status, _ = op(Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "ok")

    @patch("lib.batch_git._run")
    def test_detached_skips(self, mock_run):
        with patch("lib.batch_git._get_current_branch", return_value=""):
            mock_run.side_effect = [
                _mock_run(returncode=0),   # fetch
                _mock_run(returncode=1),   # show-ref remote target
            ]
            op = _push_one_factory(target="canary", dry_run=False, extra=[])
            r = MagicMock()
            status, detail = op(Path("/repo"), r, Path("/root"))
            self.assertEqual(status, "skip")
            self.assertIn("detached", detail)

    @patch("lib.batch_git._run")
    @patch("lib.batch_git._get_current_branch", return_value="feat")
    def test_calls_new_push_target_symlink(self, _mock_br, mock_run):
        """非 dry-run 路径调新名 symlink `push_{target}`（验证 target 参数化）。"""
        mock_run.side_effect = [
            _mock_run(returncode=0),   # fetch
            _mock_run(returncode=1),   # show-ref remote target (不存在) → cond1 通过
            _mock_run(returncode=1),   # show-ref local target (不存在) → cond2 不通过
            _mock_run(returncode=0),   # 执行 push_develop
        ]
        op = _push_one_factory(target="develop", dry_run=False, extra=[])
        r = MagicMock()
        status, _ = op(Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "ok")
        # 最后一次 _run 调用应是 push_develop
        last_call_args = mock_run.call_args_list[-1][0][0]
        self.assertEqual(last_call_args[0], "push_develop")


class TestPushAllArgparse(unittest.TestCase):
    """push_all(target, argv) 参数解析。"""

    @patch("lib.batch_git.run_batch")
    def test_parses_dry_run(self, mock_batch):
        push_all("canary", argv=["push_canary", "--dry-run"])
        _, kwargs = mock_batch.call_args
        self.assertFalse(kwargs["confirm"])
        op = kwargs["operation"]
        self.assertTrue(callable(op))

    @patch("lib.batch_git.run_batch")
    def test_confirm_always_false(self, mock_batch):
        """批量模式自动执行，无确认门（confirm=False）。"""
        push_all("develop", argv=["push_develop"])
        self.assertFalse(mock_batch.call_args[1]["confirm"])

    @patch("lib.batch_git.run_batch")
    def test_extra_passthrough(self, mock_batch):
        """--stay 等非批量参数透传给 factory 的 extra。"""
        push_all("canary", argv=["push_canary", "--stay"])
        # 通过直接调 factory 验证 extra 透传（factory 内部捕获 extra）
        captured = {}
        original = _push_one_factory

        def spy(target, dry_run, extra):
            captured["extra"] = extra
            return original(target, dry_run, extra)

        with patch("lib.batch_git._push_one_factory", side_effect=spy):
            push_all("canary", argv=["push_canary", "--stay"])
        self.assertIn("--stay", captured["extra"])

    @patch("lib.batch_git.run_batch")
    def test_exit_code_zero_when_no_failure(self, mock_batch):
        """全成功/跳过 → 退出码 0。"""
        from lib.batch_git import BatchResult as _BR
        ok = _BR(total=1)
        ok.succeeded.append(RepoResult("a", "/a", "ok"))
        mock_batch.return_value = ok
        rc = push_all("canary", argv=["push_canary"])
        self.assertEqual(rc, 0)

    @patch("lib.batch_git.run_batch")
    def test_exit_code_one_when_failed(self, mock_batch):
        """有失败 → 退出码 1（shell && / || 可感知）。"""
        from lib.batch_git import BatchResult as _BR
        bad = _BR(total=2)
        bad.failed.append(RepoResult("x", "/x", "fail", "boom"))
        mock_batch.return_value = bad
        rc = push_all("canary", argv=["push_canary"])
        self.assertEqual(rc, 1)


class TestSwitchFactory(unittest.TestCase):
    def test_returns_callable(self):
        self.assertTrue(callable(_switch_one_factory("main")))

    @patch("lib.batch_git._run")
    @patch("lib.batch_git._get_current_branch", return_value="main")
    def test_already_on_target_skips(self, _mock_br, mock_run):
        op = _switch_one_factory("main")
        r = MagicMock()
        status, detail = op(Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")
        self.assertIn("已在", detail)


class TestSyncFactory(unittest.TestCase):
    def test_returns_callable(self):
        self.assertTrue(callable(_sync_one_factory(force=False)))

    @patch("lib.batch_git._run")
    def test_fetch_fail(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1)
        op = _sync_one_factory(force=False)
        r = MagicMock()
        status, detail = op(Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "fail")
        self.assertIn("fetch", detail)

    @patch("lib.batch_git._run")
    def test_no_local_master(self, mock_run):
        mock_run.side_effect = [
            _mock_run(returncode=0),   # fetch
            _mock_run(returncode=1),   # rev-parse master (不存在)
        ]
        op = _sync_one_factory(force=False)
        r = MagicMock()
        status, detail = op(Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")

    @patch("lib.batch_git._run")
    def test_no_remote_master(self, mock_run):
        mock_run.side_effect = [
            _mock_run(returncode=0),   # fetch
            _mock_run(returncode=0),   # local master exists
            _mock_run(returncode=1),   # remote master 不存在
        ]
        op = _sync_one_factory(force=False)
        r = MagicMock()
        status, detail = op(Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")
        self.assertIn("origin/master", detail)

    @patch("lib.batch_git._run")
    def test_dirty_tree_skips(self, mock_run):
        mock_run.side_effect = [
            _mock_run(returncode=0),   # fetch
            _mock_run(returncode=0),   # local master
            _mock_run(returncode=0),   # remote master
            _mock_run(returncode=1),   # diff-index (脏)
        ]
        op = _sync_one_factory(force=False)
        r = MagicMock()
        status, detail = op(Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")
        self.assertIn("未提交", detail)

    @patch("lib.batch_git._run")
    def test_ahead_no_force_skips(self, mock_run):
        mock_run.side_effect = [
            _mock_run(returncode=0),   # fetch
            _mock_run(returncode=0),   # local master
            _mock_run(returncode=0),   # remote master
            _mock_run(returncode=0),   # dirty check ok
            _mock_run(stdout="1\t0\n"),  # rev-list: ahead 1, behind 0
            _mock_run(stdout="abc123 fix\n"),  # log
        ]
        op = _sync_one_factory(force=False)
        r = MagicMock()
        status, detail = op(Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")
        self.assertIn("领先", detail)


if __name__ == "__main__":
    unittest.main()

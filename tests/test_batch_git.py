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
    _delete_branch_one_factory,
    _delete_branch_remote_one_factory,
    _extract_error,
    _push_one_factory,
    _switch_one_factory,
    _sync_one_factory,
    notify_batch_done,
    print_repo_list,
    print_summary,
    push_all,
    run_batch,
    scan_gitlab_repos,
    scan_repos,
)
from lib.ui import reporter


def _mock_run(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def _run_op(detect_fn, repo, r, root):
    """模拟 run_batch 单仓: 跑 detect, 若 plan.execute 非空则执行, 返 (status, detail)。

    测试用 helper — 让旧式 `status, detail = op(repo, r, root)` 测试在新两阶段
    契约下继续工作（detect 现返 RepoPlan 而非直接 (status, detail)）。
    """
    plan = detect_fn(repo, r, root)
    if plan.execute is None:
        return plan.status, plan.detail
    return plan.execute(repo, plan, r, root)


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


class TestScanRepos(unittest.TestCase):
    @patch("lib.batch_git.os.walk")
    def test_finds_any_git_repo(self, mock_walk):
        """不限 remote 提供商：任何含 .git 的目录都收录。"""
        mock_walk.return_value = [
            ("/root", ["repo1", "repo2"], []),
            ("/root/repo1", [".git"], []),
            ("/root/repo2", [".git"], []),
        ]
        repos = scan_repos(Path("/root"))
        self.assertEqual(len(repos), 2)

    @patch("lib.batch_git.os.walk")
    def test_includes_github_and_bare(self, mock_walk):
        """GitHub / 裸 git 仓库（旧版会被 gitlab 过滤掉）都应收录。"""
        mock_walk.return_value = [
            ("/root", ["gh", "bare"], []),
            ("/root/gh", [".git"], []),
            ("/root/bare", [".git"], []),
        ]
        repos = scan_repos(Path("/root"))
        self.assertEqual(len(repos), 2)

    @patch("lib.batch_git.os.walk")
    def test_max_depth_no_crash(self, mock_walk):
        # os.walk mock 无法复现 dirnames.clear() 语义, 仅验证不崩
        mock_walk.return_value = [("/root/a/b", [], [])]
        scan_repos(Path("/root"), max_depth=1)

    @patch("lib.batch_git.os.walk")
    def test_removes_git_from_dirnames(self, mock_walk):
        # 同上, mock os.walk 无法验证 dirnames.remove; 仅验证入口不崩
        mock_walk.return_value = [("/root/repo", [], [])]
        scan_repos(Path("/root"))

    @patch("lib.batch_git.os.walk")
    def test_finds_submodule_git_file(self, mock_walk):
        """`.git` 作为文件（submodule / worktree）出现在 filenames 时也应识别。"""
        mock_walk.return_value = [
            ("/root", ["sub"], []),
            ("/root/sub", [], [".git"]),  # submodule: .git 是文件
        ]
        repos = scan_repos(Path("/root"))
        self.assertEqual(len(repos), 1)
        self.assertEqual(repos[0], Path("/root/sub"))

    def test_gitlab_alias_backward_compat(self):
        """旧名 scan_gitlab_repos 应仍可调用（向后兼容别名）。"""
        self.assertTrue(callable(scan_gitlab_repos))
        self.assertIs(scan_gitlab_repos, scan_repos)


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
        # status_table 标题
        self.assertIn("汇总", out)
        # 三仓都在表内
        self.assertIn("a", out)
        self.assertIn("b", out)
        self.assertIn("c", out)
        # 状态标签
        self.assertIn("成功", out)
        self.assertIn("跳过", out)
        self.assertIn("失败", out)
        # 失败详情
        self.assertIn("err", out)
        # footer 单行统计（含分母）
        self.assertIn("1/3", out)

    def test_empty_sections_omitted(self):
        r = self._make_reporter()
        result = BatchResult(total=1)
        result.succeeded.append(RepoResult("only", "/o", "ok"))
        buf, redir = self._capture()
        with redir:
            print_summary(r, "汇总", result)
        out = buf.getvalue()
        self.assertIn("成功", out)
        # 无失败/跳过 → footer 不含对应段
        self.assertNotIn("失败", out)
        self.assertNotIn("跳过", out)
        self.assertIn("1/1", out)

    def test_failed_without_detail(self):
        r = self._make_reporter()
        result = BatchResult(total=1)
        result.failed.append(RepoResult("x", "/x", "fail"))  # detail 默认空
        buf, redir = self._capture()
        with redir:
            print_summary(r, "汇总", result)
        out = buf.getvalue()
        self.assertIn("x", out)
        self.assertIn("失败", out)


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
        op = _push_one_factory(target="canary", dry_run=True, auto_commit=False, extra=[])
        self.assertTrue(callable(op))

    @patch("lib.batch_git._run")
    @patch("lib.batch_git._get_current_branch", return_value="feat")
    def test_dry_run_skips_with_preview(self, _mock_br, mock_run):
        # fetch ok, remote target 不存在 (cond1 通过), local target 不存在 (cond2 不通过)
        # cond1 || cond2 → 满足 → dry_run 模式不执行, 返 skip + 预览 detail
        mock_run.side_effect = [
            _mock_run(returncode=0),   # fetch
            _mock_run(returncode=1),   # show-ref remote target (不存在)
            _mock_run(returncode=1),   # show-ref local target (不存在)
        ]
        op = _push_one_factory(target="canary", dry_run=True, auto_commit=False, extra=[])
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")
        self.assertIn("dry-run", detail)

    @patch("lib.batch_git._run")
    def test_detached_skips(self, mock_run):
        with patch("lib.batch_git._get_current_branch", return_value=""):
            mock_run.side_effect = [
                _mock_run(returncode=0),   # fetch
                _mock_run(returncode=1),   # show-ref remote target
            ]
            op = _push_one_factory(target="canary", dry_run=False, auto_commit=False, extra=[])
            r = MagicMock()
            status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
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
        op = _push_one_factory(target="develop", dry_run=False, auto_commit=False, extra=[])
        r = MagicMock()
        status, _ = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "ok")
        # 最后一次 _run 调用应是 push_develop
        last_call_args = mock_run.call_args_list[-1][0][0]
        self.assertEqual(last_call_args[0], "push_develop")

    @patch("lib.batch_git._run")
    @patch("lib.batch_git._get_current_branch", return_value="feat")
    def test_fail_detail_is_concise(self, _mock_br, mock_run):
        """execute 失败: detail 简短（退出码）, 子进程输出已 capture_output=False 实时直吐 stderr。

        新两阶段契约: push_{target} 子进程 capture_output=False, 失败 detail 仅标退出码;
        旧的 _extract_error 提取 + warn 重打逻辑已移除（实时流式无需再攒着重放）。
        """
        mock_run.side_effect = [
            _mock_run(returncode=0),   # fetch
            _mock_run(returncode=1),   # show-ref remote target (不存在) → cond1 通过
            _mock_run(returncode=1),   # show-ref local target → cond2 不通过
            _mock_run(returncode=1),   # push_canary 失败 (capture_output=False, 不读 stdout)
        ]
        op = _push_one_factory(target="canary", dry_run=False, auto_commit=False, extra=[])
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "fail")
        # detail 单行, 含 push_canary + 退出码
        self.assertEqual(detail.count("\n"), 0)
        self.assertIn("push_canary", detail)
        self.assertIn("1", detail)
        # execute 跑了 push_canary 子进程（第 4 次 _run 调用）
        last_call_args = mock_run.call_args_list[-1][0][0]
        self.assertEqual(last_call_args[0], "push_canary")


class TestExtractError(unittest.TestCase):
    def test_matches_conflict_keyword(self):
        out = "Git 自动化工作流\nstep: merge\nCONFLICT (content): Merge conflict in foo.go\nend"
        self.assertEqual(
            _extract_error(out, 1, "push_canary"),
            "CONFLICT (content): Merge conflict in foo.go",
        )

    def test_matches_rejected_keyword(self):
        out = "push\n! [rejected] HEAD -> canary (non-fast-forward)\ndone"
        self.assertEqual(
            _extract_error(out, 1, "push_canary"),
            "! [rejected] HEAD -> canary (non-fast-forward)",
        )

    def test_matches_last_when_multiple(self):
        out = "error: first\nstep\nfatal: real cause"
        self.assertEqual(_extract_error(out, 1, "push_canary"), "fatal: real cause")

    def test_no_match_returns_last_line(self):
        out = "line1\n普通输出\nlast line here"
        self.assertEqual(_extract_error(out, 1, "push_canary"), "last line here")

    def test_empty_output_fallback(self):
        self.assertEqual(_extract_error("", 1, "push_canary"), "push_canary 失败 (exit 1)")

    def test_truncates_long_line(self):
        long_line = "CONFLICT " + "x" * 500
        self.assertLessEqual(len(_extract_error(long_line, 1, "push_canary")), 200)


class TestPushAllArgparse(unittest.TestCase):
    """push_all(target, argv) 参数解析。"""

    @patch("lib.batch_git.run_batch")
    def test_parses_dry_run(self, mock_batch):
        push_all("canary", argv=["push_canary", "--dry-run"])
        _, kwargs = mock_batch.call_args
        self.assertFalse(kwargs["confirm"])
        detect = kwargs["detect"]
        self.assertTrue(callable(detect))

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

        def spy(target, dry_run, auto_commit, extra):
            captured["extra"] = extra
            return original(target, dry_run, auto_commit, extra)

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
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")
        self.assertIn("已在", detail)


class TestSyncFactory(unittest.TestCase):
    def test_returns_callable(self):
        self.assertTrue(callable(_sync_one_factory("master", force=False)))

    @patch("lib.batch_git._run")
    def test_fetch_fail(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1)
        op = _sync_one_factory("master", force=False)
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "fail")
        self.assertIn("fetch", detail)

    @patch("lib.batch_git._resolve_main_branch", return_value="master")
    @patch("lib.batch_git._run")
    def test_no_local_master(self, mock_run, _mock_resolve):
        mock_run.side_effect = [
            _mock_run(returncode=0),   # fetch
            _mock_run(returncode=1),   # rev-parse master (不存在)
        ]
        op = _sync_one_factory("master", force=False)
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")

    @patch("lib.batch_git._resolve_main_branch", return_value="master")
    @patch("lib.batch_git._run")
    def test_no_remote_master(self, mock_run, _mock_resolve):
        mock_run.side_effect = [
            _mock_run(returncode=0),   # fetch
            _mock_run(returncode=0),   # local master exists
            _mock_run(returncode=1),   # remote master 不存在
        ]
        op = _sync_one_factory("master", force=False)
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")
        self.assertIn("origin/master", detail)

    @patch("lib.batch_git._resolve_main_branch", return_value="master")
    @patch("lib.batch_git._run")
    def test_dirty_tree_fails(self, mock_run, _mock_resolve):
        mock_run.side_effect = [
            _mock_run(returncode=0),   # fetch
            _mock_run(returncode=0),   # local master
            _mock_run(returncode=0),   # remote master
            _mock_run(returncode=1),   # diff-index (脏)
            _mock_run(stdout="M file.py\n"),  # git status --porcelain (_dirty_detail)
        ]
        op = _sync_one_factory("master", force=False)
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "fail")
        self.assertIn("未提交", detail)

    @patch("lib.batch_git._resolve_main_branch", return_value="master")
    @patch("lib.batch_git._run")
    def test_ahead_no_force_skips(self, mock_run, _mock_resolve):
        mock_run.side_effect = [
            _mock_run(returncode=0),   # fetch
            _mock_run(returncode=0),   # local master
            _mock_run(returncode=0),   # remote master
            _mock_run(returncode=0),   # dirty check ok
            _mock_run(stdout="1\t0\n"),  # rev-list: ahead 1, behind 0
            _mock_run(stdout="abc123 fix\n"),  # log
        ]
        op = _sync_one_factory("master", force=False)
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")
        self.assertIn("领先", detail)

    @patch("lib.batch_git._run")
    def test_current_branch_mode_uses_show_current(self, mock_run):
        """branch=None → 用 git branch --show-current 取当前分支并对其同步。"""
        mock_run.side_effect = [
            _mock_run(returncode=0),        # fetch
            _mock_run(stdout="feat\n"),     # branch --show-current → feat
            _mock_run(returncode=0),        # rev-parse feat
            _mock_run(returncode=0),        # rev-parse origin/feat
            _mock_run(returncode=0),        # dirty ok
            _mock_run(stdout="0\t2\n"),     # rev-list: ahead 0, behind 2
            _mock_run(stdout="feat\n"),     # branch --show-current (已在该分支)
            _mock_run(returncode=0),        # reset --hard
            _mock_run(stdout="abc1234\n"),  # rev-parse --short origin/feat
        ]
        op = _sync_one_factory(None, force=False)
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "ok")
        self.assertIn("快进 2", detail)

    @patch("lib.batch_git._run")
    def test_current_branch_detached_skips(self, mock_run):
        """branch=None 且 detached HEAD → skip。"""
        mock_run.side_effect = [
            _mock_run(returncode=0),        # fetch
            _mock_run(stdout=""),           # branch --show-current → 空
        ]
        op = _sync_one_factory(None, force=False)
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")
        self.assertIn("detached", detail)

    @patch("lib.batch_git._resolve_main_branch", return_value="master")
    @patch("lib.batch_git._run")
    def test_force_resets_when_ahead(self, mock_run, _mock_resolve):
        """force=True 且本地领先 → 硬 reset 丢弃。"""
        mock_run.side_effect = [
            _mock_run(returncode=0),        # fetch
            _mock_run(returncode=0),        # local master
            _mock_run(returncode=0),        # remote master
            _mock_run(returncode=0),        # dirty ok
            _mock_run(stdout="3\t0\n"),     # rev-list: ahead 3
            _mock_run(stdout="master\n"),   # branch --show-current
            _mock_run(returncode=0),        # reset --hard
            _mock_run(stdout="abc1234\n"),  # rev-parse --short
        ]
        op = _sync_one_factory("master", force=True)
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "ok")
        self.assertIn("强制对齐", detail)
        self.assertIn("丢弃 3", detail)


class TestDeleteBranchFactory(unittest.TestCase):
    @patch("lib.batch_git._get_current_branch", return_value="master")
    @patch("lib.batch_git._run")
    def test_current_branch_skips(self, mock_run, _mock_br):
        """当前分支 == 目标 → skip。"""
        op = _delete_branch_one_factory("master", force=False)
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")
        self.assertIn("先 switch", detail)
        mock_run.assert_not_called()

    @patch("lib.batch_git._get_current_branch", return_value="master")
    @patch("lib.batch_git._run")
    def test_no_local_branch_skips(self, mock_run, _mock_br):
        """本地无该分支 → skip。"""
        mock_run.return_value = _mock_run(returncode=1)  # show-ref 失败
        op = _delete_branch_one_factory("feat", force=False)
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")

    @patch("lib.batch_git._get_current_branch", return_value="master")
    @patch("lib.batch_git._run")
    def test_delete_ok(self, mock_run, _mock_br):
        """正常删除 → ok。"""
        mock_run.side_effect = [
            _mock_run(returncode=0),   # show-ref 存在
            _mock_run(returncode=0),   # git branch -d
        ]
        op = _delete_branch_one_factory("feat", force=False)
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "ok")
        self.assertIn("已删本地 feat", detail)
        # 验证用了 -d 而非 -D
        self.assertEqual(mock_run.call_args_list[1].args[0], ["git", "branch", "-d", "feat"])

    @patch("lib.batch_git._get_current_branch", return_value="master")
    @patch("lib.batch_git._run")
    def test_force_uses_capital_d(self, mock_run, _mock_br):
        """force=True → 用 -D。"""
        mock_run.side_effect = [
            _mock_run(returncode=0),   # show-ref
            _mock_run(returncode=0),   # git branch -D
        ]
        op = _delete_branch_one_factory("feat", force=True)
        r = MagicMock()
        status, _ = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "ok")
        self.assertEqual(mock_run.call_args_list[1].args[0], ["git", "branch", "-D", "feat"])

    @patch("lib.batch_git._get_current_branch", return_value="master")
    @patch("lib.batch_git._run")
    def test_unmerged_without_force_skips(self, mock_run, _mock_br):
        """未合并 + 无 force → skip（提示 --force）。"""
        mock_run.side_effect = [
            _mock_run(returncode=0),   # show-ref
            _mock_run(returncode=1, stderr="error: The branch 'feat' is not fully merged."),
        ]
        op = _delete_branch_one_factory("feat", force=False)
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")
        self.assertIn("未合并", detail)


class TestDeleteBranchRemoteFactory(unittest.TestCase):
    @patch("lib.batch_git._run")
    def test_no_remote_ref_skips(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1)  # show-ref 失败
        op = _delete_branch_remote_one_factory("feat", "origin")
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "skip")

    @patch("lib.batch_git._run")
    def test_delete_ok_and_prune(self, mock_run):
        """删除成功后 fetch --prune 清 tracking ref。"""
        mock_run.side_effect = [
            _mock_run(returncode=0),   # show-ref 存在
            _mock_run(returncode=0),   # push --delete
            _mock_run(returncode=0),   # fetch --prune
        ]
        op = _delete_branch_remote_one_factory("feat", "origin")
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "ok")
        self.assertIn("已删 origin/feat", detail)
        self.assertEqual(mock_run.call_args_list[1].args[0],
                         ["git", "push", "origin", "--delete", "feat"])

    @patch("lib.batch_git._run")
    def test_push_fail_returns_fail(self, mock_run):
        mock_run.side_effect = [
            _mock_run(returncode=0),   # show-ref
            _mock_run(returncode=1, stderr="remote: permission denied"),
        ]
        op = _delete_branch_remote_one_factory("feat", "origin")
        r = MagicMock()
        status, detail = _run_op(op, Path("/repo"), r, Path("/root"))
        self.assertEqual(status, "fail")


class TestRunBatchParallel(unittest.TestCase):
    """run_batch 并行模式：完成序无关、并发上限可调、per-repo 输出不交错、Ctrl-C 取消。"""

    def _op_factory(self, statuses_by_name: dict[str, tuple[str, str]], log_lines: dict[str, list[str]] | None = None):
        """构造测试用 detect：按 repo name 返 RepoPlan（status/detail 来自表），并往 r 打多行日志。

        status="ok" 时塞一个 execute 桩（直接返原 detail），模拟两阶段 execute 段。
        """
        from lib.batch_git import RepoPlan
        log_lines = log_lines or {}
        def _execute_factory(detail):
            def _exec(repo, plan, r, _root):
                return "ok", detail
            return _exec
        def _detect(repo: Path, r, _root: Path) -> RepoPlan:
            name = repo.name
            for line in log_lines.get(name, []):
                r.info(line)
            status, detail = statuses_by_name.get(name, ("ok", ""))
            if status == "ok":
                return RepoPlan(status="ok", detail=detail, execute=_execute_factory(detail))
            return RepoPlan(status=status, detail=detail)
        return _detect

    @patch("lib.batch_git.notify_batch_done")
    @patch("lib.batch_git.scan_repos")
    def test_collects_all_repos_order_independent(self, mock_scan, mock_notify):
        """完成序非提交序，但汇总必须含全部仓库。"""
        mock_scan.return_value = [Path("/r/a"), Path("/r/b"), Path("/r/c")]
        op = self._op_factory({"a": ("ok", ""), "b": ("skip", "已对齐"), "c": ("fail", "boom")})
        with patch("lib.batch_git.sys.stdin.isatty", return_value=False):
            result = run_batch("t", Path("/r"), op, confirm=False)
        names_ok = sorted(x.name for x in result.succeeded)
        names_skip = sorted(x.name for x in result.skipped)
        names_fail = sorted(x.name for x in result.failed)
        self.assertEqual(names_ok, ["a"])
        self.assertEqual(names_skip, ["b"])
        self.assertEqual(names_fail, ["c"])
        self.assertEqual(result.total, 3)
        mock_notify.assert_called_once()

    @patch("lib.batch_git.notify_batch_done")
    @patch("lib.batch_git.scan_repos")
    def test_concurrency_env_override(self, mock_scan, mock_notify):
        """BATCH_CONCURRENCY 可调（覆盖默认 4）。"""
        mock_scan.return_value = [Path("/r/a"), Path("/r/b")]
        op = self._op_factory({"a": ("ok", ""), "b": ("ok", "")})
        with patch.dict("os.environ", {"BATCH_CONCURRENCY": "8"}):
            with patch("lib.batch_git.ThreadPoolExecutor") as mock_pool:
                mock_pool.return_value.__enter__.return_value = mock_pool.return_value
                mock_pool.return_value.submit.side_effect = lambda *a, **k: _FakeFuture()
                try:
                    run_batch("t", Path("/r"), op, confirm=False)
                except Exception:
                    pass
                _, kwargs = mock_pool.call_args
                self.assertEqual(kwargs.get("max_workers"), 8)

    @patch("lib.batch_git.notify_batch_done")
    @patch("lib.batch_git.scan_repos")
    def test_per_repo_log_not_interleaved(self, mock_scan, mock_notify):
        """每个仓库的多行日志在 buffer 中保持顺序完整。"""
        mock_scan.return_value = [Path("/r/a"), Path("/r/b")]
        op = self._op_factory(
            {"a": ("ok", ""), "b": ("ok", "")},
            log_lines={"a": ["a-step-1", "a-step-2", "a-step-3"],
                       "b": ["b-step-1", "b-step-2", "b-step-3"]},
        )
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            with patch("lib.batch_git.sys.stdin.isatty", return_value=False):
                run_batch("t", Path("/r"), op, confirm=False)
        out = buf.getvalue()
        # 两个仓库的 step 各自连续（不被对方日志插入）
        for name in ("a", "b"):
            steps = [f"{name}-step-{i}" for i in (1, 2, 3)]
            idx = [out.find(s) for s in steps]
            self.assertTrue(all(i >= 0 for i in idx), f"missing steps for {name}")
            self.assertEqual(idx, sorted(idx), f"steps out of order for {name}")
            # 三步之间不应插入对方 step
            segment = out[idx[0]:idx[-1]]
            other = "b" if name == "a" else "a"
            self.assertNotIn(f"{other}-step-", segment)

    @patch("lib.batch_git.notify_batch_done")
    @patch("lib.batch_git.scan_repos")
    def test_exception_in_op_recorded_as_fail(self, mock_scan, mock_notify):
        """detect 抛异常 → 记为 fail，detail 含异常信息。"""
        mock_scan.return_value = [Path("/r/a")]
        def _boom(repo, r, _root):
            raise RuntimeError("boom-error")
        with patch("lib.batch_git.sys.stdin.isatty", return_value=False):
            result = run_batch("t", Path("/r"), _boom, confirm=False)
        self.assertEqual(len(result.failed), 1)
        self.assertIn("boom-error", result.failed[0].detail)

    @patch("lib.batch_git.os._exit", side_effect=RuntimeError("exit-called"))
    @patch("lib.batch_git.notify_batch_done")
    @patch("lib.batch_git.scan_repos")
    def test_keyboard_interrupt_cancels(self, mock_scan, mock_notify, mock_exit):
        """Ctrl-C 在检测阶段 → os._exit(130) 中止（worker 不可中断, 强退防卡死）。"""
        mock_scan.return_value = [Path("/r/a"), Path("/r/b")]
        op = self._op_factory({"a": ("ok", ""), "b": ("ok", "")})
        with patch("lib.batch_git.as_completed", side_effect=KeyboardInterrupt):
            with patch("lib.batch_git.sys.stdin.isatty", return_value=False):
                with self.assertRaises(RuntimeError):
                    run_batch("t", Path("/r"), op, confirm=False)
        # os._exit(130) 被调用
        mock_exit.assert_called_once_with(130)


class _FakeFuture:
    """最小 Future 桩（仅用于 max_workers 断言路径）。"""
    def result(self):
        return (0, "", RepoResult("a", "/r/a", "ok"))
    def add_done_callback(self, _cb):
        pass


if __name__ == "__main__":
    unittest.main()

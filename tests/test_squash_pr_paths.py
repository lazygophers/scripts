#!/usr/bin/env python3
"""squash_pr_wf 分支路径测试：失败回滚、演练模式、mr 对接、冲突探测回退。

用假的 git 执行层（按命令前缀匹配返回码），把 run_squash_pr 的每条分支单独走一遍。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import lib.squash_pr_wf as W

_DEFAULT_RULES = {
    "rev-list": (0, "1\t0\n", ""),
    "rev-parse --verify --quiet": (1, "", ""),
    "merge-base": (0, "MB123\n", ""),
    "log --no-merges": (0, "feat: a\nfeat: b\n", ""),
}


class _FakeGit:
    """按命令前缀匹配的 git 替身；记录所有调用便于断言。"""

    def __init__(self, rules=None):
        self.rules = dict(_DEFAULT_RULES)
        self.rules.update(rules or {})
        self.calls: list[str] = []

    def __call__(self, cmd, **kwargs):
        args = list(cmd)
        if args and args[0] == "git":
            args = args[1:]
        sig = " ".join(args)
        self.calls.append(sig)
        best = ""
        for key in self.rules:
            if sig.startswith(key) and len(key) > len(best):
                best = key
        rc, out, err = self.rules.get(best, (0, "", ""))
        return MagicMock(returncode=rc, stdout=out, stderr=err)

    def ran(self, prefix: str) -> bool:
        return any(c.startswith(prefix) for c in self.calls)


def _run_flow(rules=None, *, conflicts=None, remote_exists=None,
              notify_error=False, fetch_output="", **kwargs):
    """跑 run_squash_pr，git 层全部替身化。返回 (result, fake_git, reporter)。"""
    fake = _FakeGit(rules)
    r = MagicMock()
    conflicts = conflicts or [(False, []), (False, [])]
    notify_kw = {"side_effect": RuntimeError("no say")} if notify_error else {}
    with patch.object(W, "run", fake), \
         patch.object(W, "_git", fake), \
         patch.object(W, "check_bit_clean"), \
         patch.object(W, "get_current_branch", return_value="master"), \
         patch.object(W, "remote_branch_exists",
                      side_effect=remote_exists or (lambda b, **kw: b == "source")), \
         patch.object(W, "retry_command",
                      return_value=MagicMock(ok=True, last_output=fetch_output)), \
         patch.object(W, "detect_conflict", side_effect=list(conflicts)), \
         patch.object(W, "notify", **notify_kw):
        res = W.run_squash_pr("source", "target", r=r, no_mr=True, **kwargs)
    return res, fake, r


class TestStripRemotePrefix(unittest.TestCase):
    def test_strips_matching_remote(self):
        self.assertEqual(W._strip_remote_prefix("origin/staging", "origin"), "staging")

    def test_leaves_other_names(self):
        self.assertEqual(W._strip_remote_prefix("upstream/x", "origin"), "upstream/x")


class TestParseMergeTreeOutput(unittest.TestCase):
    def test_skips_index_lines(self):
        out = "\n".join([
            "abc123treehash",
            "100644 " + "a" * 40 + " 1 src/app.go",
            "src/app.go",
            "CONFLICT (content): merge conflict in src/app.go",
        ])
        self.assertEqual(W._parse_merge_tree_output(out), ["src/app.go"])


class TestDetectConflictFallback(unittest.TestCase):
    def test_old_git_falls_back_and_warns(self):
        r = MagicMock()
        with patch.object(W, "run", return_value=MagicMock(returncode=129, stdout="", stderr="")), \
             patch.object(W, "_detect_conflict_via_merge", return_value=(True, ["a.txt"])) as fb:
            has, files = W.detect_conflict("a", "origin/b", r=r)
        self.assertTrue(has)
        self.assertEqual(files, ["a.txt"])
        r.warn.assert_called_once()
        fb.assert_called_once()


class TestDetectConflictViaMerge(unittest.TestCase):
    def test_clean_merge_aborts_and_restores(self):
        fake = _FakeGit()
        with patch.object(W, "run", fake), \
             patch.object(W, "get_current_branch", return_value="master"):
            self.assertEqual(W._detect_conflict_via_merge("a", "b"), (False, []))
        self.assertTrue(fake.ran("checkout --detach a"))
        self.assertTrue(fake.ran("merge --abort"))
        self.assertTrue(fake.ran("reset --hard HEAD"))
        self.assertTrue(fake.ran("checkout master"))

    def test_conflict_lists_unmerged_files(self):
        fake = _FakeGit({
            "merge --no-commit": (1, "", "CONFLICT"),
            "diff --name-only --diff-filter=U": (0, "a.txt\nb.txt\n\n", ""),
        })
        with patch.object(W, "run", fake), \
             patch.object(W, "get_current_branch", return_value="master"):
            has, files = W._detect_conflict_via_merge("a", "b")
        self.assertTrue(has)
        self.assertEqual(files, ["a.txt", "b.txt"])
        self.assertTrue(fake.ran("merge --abort"))

    def test_detached_head_skips_restore(self):
        fake = _FakeGit()
        with patch.object(W, "run", fake), \
             patch.object(W, "get_current_branch", return_value=""):
            W._detect_conflict_via_merge("a", "b")
        self.assertFalse(fake.ran("checkout master"))


class TestRollbackAndFail(unittest.TestCase):
    def test_rollback_deletes_pushed_and_local_branch(self):
        fake = _FakeGit()
        state = W._RollbackState(original_branch="master", pr_branch="source_pr",
                                 pr_branch_created_local=True, pr_branch_pushed=True)
        with patch.object(W, "run", fake):
            W._rollback(state, r=MagicMock())
        self.assertTrue(fake.ran("push origin --delete source_pr"))
        self.assertTrue(fake.ran("checkout master"))
        self.assertTrue(fake.ran("branch -D source_pr"))

    def test_rollback_restores_preexisting_branch_and_keeps_remote(self):
        """复用的分支回滚：本地还原到原 sha，远端不删（删了会关 PR）。"""
        fake = _FakeGit()
        state = W._RollbackState(original_branch="master", pr_branch="source_pr",
                                 pr_branch_preexisting_local=True,
                                 pr_branch_orig_sha="OLDSHA",
                                 pr_branch_remote_preexisting=True,
                                 pr_branch_pushed=True)
        with patch.object(W, "run", fake):
            W._rollback(state, r=MagicMock())
        self.assertTrue(fake.ran("checkout master"))
        self.assertTrue(fake.ran("branch -f source_pr OLDSHA"))
        self.assertFalse(fake.ran("branch -D source_pr"))
        self.assertFalse(fake.ran("push origin --delete source_pr"))

    def test_rollback_without_original_branch_skips_checkout(self):
        fake = _FakeGit()
        state = W._RollbackState(pr_branch="source_pr", pr_branch_created_local=True)
        with patch.object(W, "run", fake):
            W._rollback(state, r=MagicMock())
        self.assertFalse(fake.ran("checkout "))
        self.assertTrue(fake.ran("branch -D source_pr"))

    def test_fail_swallows_notify_error(self):
        with patch.object(W, "_rollback"), \
             patch.object(W, "notify", side_effect=RuntimeError("no say")):
            res = W._fail("boom", W._RollbackState(), r=MagicMock())
        self.assertEqual(res.returncode, 1)


class TestRunSquashPrGuards(unittest.TestCase):
    def test_default_reporter_created_when_absent(self):
        fake = _FakeGit()
        with patch.object(W, "run", fake), \
             patch.object(W, "_git", fake), \
             patch.object(W, "check_bit_clean", side_effect=W.GitError("dirty")), \
             patch.object(W, "reporter") as mock_reporter, \
             patch.object(W, "notify"):
            res = W.run_squash_pr("source", "target")
        self.assertEqual(res.returncode, 1)
        mock_reporter.assert_called_once_with(stderr=True)

    def test_detached_head_aborts(self):
        fake = _FakeGit()
        with patch.object(W, "run", fake), \
             patch.object(W, "_git", fake), \
             patch.object(W, "check_bit_clean"), \
             patch.object(W, "get_current_branch", return_value=""), \
             patch.object(W, "notify"):
            res = W.run_squash_pr("source", "target", r=MagicMock())
        self.assertEqual(res.returncode, 1)

    def test_fetch_target_failure_aborts(self):
        r = MagicMock()
        with patch.object(W, "run", _FakeGit()), \
             patch.object(W, "_git", _FakeGit()), \
             patch.object(W, "check_bit_clean"), \
             patch.object(W, "get_current_branch", return_value="master"), \
             patch.object(W, "retry_command",
                          return_value=MagicMock(ok=False, last_output="no route")), \
             patch.object(W, "notify"):
            res = W.run_squash_pr("source", "target", r=r)
        self.assertEqual(res.returncode, 1)

    def test_source_fetch_failure_only_warns(self):
        r = MagicMock()
        with patch.object(W, "run", _FakeGit()), \
             patch.object(W, "_git", _FakeGit()), \
             patch.object(W, "check_bit_clean"), \
             patch.object(W, "get_current_branch", return_value="master"), \
             patch.object(W, "remote_branch_exists", return_value=True), \
             patch.object(W, "retry_command", side_effect=[
                 MagicMock(ok=True, last_output="ok\n"),
                 MagicMock(ok=False, last_output="flaky"),
             ]), \
             patch.object(W, "detect_conflict", return_value=(True, ["x"])), \
             patch.object(W, "notify"):
            res = W.run_squash_pr("source", "target", r=r)
        # 冲突让流程停在预演 #1，但 source fetch 失败只该是 warn
        self.assertEqual(res.returncode, 1)
        self.assertTrue(any("fetch source 失败" in str(c) for c in r.warn.call_args_list))

    def test_fetch_output_is_echoed(self):
        res, _, r = _run_flow(fetch_output="From origin\n * branch  target\n")
        self.assertEqual(res.returncode, 0)
        self.assertTrue(any("From origin" in str(c) for c in r.output.call_args_list))

    def test_missing_remote_source_is_informational(self):
        res, fake, r = _run_flow(remote_exists=lambda b, **kw: False,
                                 conflicts=[(False, []), (False, [])])
        self.assertEqual(res.returncode, 0)
        self.assertTrue(any("无远端分支" in str(c) for c in r.info.call_args_list))

    def test_local_target_checkout_failure_aborts(self):
        res, fake, _ = _run_flow({
            "rev-parse --verify --quiet target": (0, "sha\n", ""),
            "checkout target": (1, "", "cannot checkout"),
        })
        self.assertEqual(res.returncode, 1)
        # checkout 失败后不得 reset --hard，否则会毁掉起始分支
        self.assertFalse(fake.ran("reset --hard origin/target"))

    def test_local_target_is_refreshed(self):
        res, fake, _ = _run_flow({"rev-parse --verify --quiet target": (0, "sha\n", "")})
        self.assertEqual(res.returncode, 0)
        self.assertTrue(fake.ran("reset --hard origin/target"))

    def test_merge_base_failure_aborts(self):
        res, _, _ = _run_flow({"merge-base": (1, "", "no merge base")})
        self.assertEqual(res.returncode, 1)

    def test_source_behind_remote_aborts(self):
        res, _, _ = _run_flow({"rev-list": (0, "0\t3\n", "")})
        self.assertEqual(res.returncode, 1)


class TestRunSquashPrConflictAndBranchSetup(unittest.TestCase):
    def test_first_conflict_preview_reports_files(self):
        # notify 抛错也不能改变返回值（语音失败不影响主流程）
        res, fake, r = _run_flow(conflicts=[(True, ["a.txt", "b.txt"])], notify_error=True)
        self.assertEqual(res.returncode, 1)
        self.assertEqual(res.conflict_files, ["a.txt", "b.txt"])
        self.assertFalse(fake.ran("checkout -B source_pr"))

    def test_existing_pr_branch_reused_not_deleted(self):
        """本地 + 远端都已存在 → 复用重置，force-with-lease push，不删分支。"""
        res, fake, _ = _run_flow(
            {"rev-parse --verify --quiet source_pr": (0, "sha\n", ""),
             "rev-parse source_pr": (0, "OLDSHA\n", "")},
            remote_exists=lambda b, **kw: True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertTrue(fake.ran("checkout -B source_pr"))
        self.assertTrue(fake.ran("push --force-with-lease origin source_pr"))
        self.assertFalse(fake.ran("branch -D source_pr"))
        self.assertFalse(fake.ran("push origin --delete source_pr"))

    def test_existing_pr_branch_conflict_rolls_back_to_orig_sha(self):
        """复用分支 + 预演 #2 冲突 → 还原本地到原 sha，不删远端。"""
        res, fake, _ = _run_flow(
            {"rev-parse --verify --quiet source_pr": (0, "sha\n", ""),
             "rev-parse source_pr": (0, "OLDSHA\n", "")},
            remote_exists=lambda b, **kw: True,
            conflicts=[(False, []), (True, ["c.txt"])],
        )
        self.assertEqual(res.returncode, 1)
        self.assertTrue(fake.ran("branch -f source_pr OLDSHA"))
        self.assertFalse(fake.ran("branch -D source_pr"))
        self.assertFalse(fake.ran("push origin --delete source_pr"))

    def test_checkout_source_failure_aborts(self):
        res, fake, _ = _run_flow({"checkout source": (1, "", "no such branch")})
        self.assertEqual(res.returncode, 1)
        self.assertFalse(fake.ran("checkout -B source_pr"))

    def test_create_pr_branch_failure_returns_to_original(self):
        res, fake, _ = _run_flow({"checkout -B source_pr": (1, "", "exists")})
        self.assertEqual(res.returncode, 1)
        self.assertTrue(fake.ran("checkout master"))


class TestRunSquashPrDryRun(unittest.TestCase):
    def test_dry_run_rolls_back_and_skips_commit(self):
        res, fake, r = _run_flow(dry_run=True)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.pr_branch, "source_pr")
        self.assertEqual(res.merge_base, "MB123")
        self.assertIn("feat: a", res.message)
        self.assertFalse(fake.ran("commit"))
        self.assertFalse(fake.ran("push -u"))
        self.assertTrue(fake.ran("branch -D source_pr"))

    def test_dry_run_mentions_mr_when_enabled(self):
        fake = _FakeGit()
        r = MagicMock()
        with patch.object(W, "run", fake), \
             patch.object(W, "_git", fake), \
             patch.object(W, "check_bit_clean"), \
             patch.object(W, "get_current_branch", return_value="master"), \
             patch.object(W, "remote_branch_exists", return_value=False), \
             patch.object(W, "retry_command", return_value=MagicMock(ok=True, last_output="")), \
             patch.object(W, "detect_conflict", return_value=(False, [])), \
             patch.object(W, "notify"):
            res = W.run_squash_pr("source", "target", r=r, dry_run=True)
        self.assertEqual(res.returncode, 0)
        self.assertTrue(any("push 后调 mr" in str(c) for c in r.info.call_args_list))


class TestRunSquashPrCommitPushAndMr(unittest.TestCase):
    def test_commit_failure_aborts(self):
        res, _, _ = _run_flow({"commit": (1, "", "nothing to commit")})
        self.assertEqual(res.returncode, 1)

    def test_second_conflict_preview_rolls_back(self):
        res, fake, r = _run_flow(conflicts=[(False, []), (True, ["c.txt"])])
        self.assertEqual(res.returncode, 1)
        self.assertFalse(fake.ran("push -u origin source_pr"))
        self.assertTrue(fake.ran("branch -D source_pr"))

    def test_push_failure_aborts(self):
        res, _, _ = _run_flow({"push -u origin source_pr": (1, "", "rejected")})
        self.assertEqual(res.returncode, 1)

    def test_no_mr_stops_after_push(self):
        res, fake, _ = _run_flow()
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.pr_branch, "source_pr")
        self.assertTrue(fake.ran("push -u origin source_pr"))

    def _flow_with_mr(self, mr_rc):
        fake = _FakeGit()
        r = MagicMock()
        with patch.object(W, "run", fake), \
             patch.object(W, "_git", fake), \
             patch.object(W, "check_bit_clean"), \
             patch.object(W, "get_current_branch", return_value="master"), \
             patch.object(W, "remote_branch_exists", return_value=False), \
             patch.object(W, "retry_command", return_value=MagicMock(ok=True, last_output="")), \
             patch.object(W, "detect_conflict", return_value=(False, [])), \
             patch.object(W, "_call_mr", return_value=mr_rc) as call_mr, \
             patch.object(W, "notify", side_effect=RuntimeError("no say")):
            res = W.run_squash_pr("source", "target", r=r)
        return res, call_mr, r

    def test_mr_success_completes(self):
        res, call_mr, _ = self._flow_with_mr(0)
        self.assertEqual(res.returncode, 0)
        call_mr.assert_called_once_with("target")

    def test_mr_failure_keeps_pushed_branch(self):
        res, _, r = self._flow_with_mr(2)
        self.assertEqual(res.returncode, 1)
        self.assertEqual(res.pr_branch, "source_pr")
        r.warn.assert_called()


class TestCallMr(unittest.TestCase):
    def test_delegates_to_run_mr(self):
        with patch("lib.mr_wf.run_mr", return_value=0) as run_mr:
            self.assertEqual(W._call_mr("target"), 0)
        run_mr.assert_called_once_with("target")

    def test_exception_becomes_exit_one(self):
        with patch("lib.mr_wf.run_mr", side_effect=RuntimeError("boom")):
            self.assertEqual(W._call_mr("target"), 1)


if __name__ == "__main__":
    unittest.main()

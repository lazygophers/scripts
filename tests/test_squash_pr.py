#!/usr/bin/env python3
"""Tests for lib.squash_pr_wf."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.squash_pr_wf import (
    _parse_merge_tree_output,
    aggregate_message,
    detect_conflict,
    fallback_message,
    pr_branch_name,
    run_squash_pr,
)


class TestPrBranchName(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(pr_branch_name("feat-123"), "feat-123_pr")

    def test_with_slash(self):
        self.assertEqual(pr_branch_name("user/feat"), "user/feat_pr")


class TestFallbackMessage(unittest.TestCase):
    def test_format(self):
        self.assertEqual(fallback_message("feat", "main"), "squash: feat → main")


class TestAggregateMessage(unittest.TestCase):
    def test_empty_falls_back(self):
        self.assertEqual(aggregate_message([], "feat", "main"), "squash: feat → main")

    def test_all_merge_noise_falls_back(self):
        subjects = [
            "Merge branch 'main' into feat",
            "Merge pull request #42",
            "Merge tag v1.2",
            "Merge remote-tracking branch 'origin/main'",
        ]
        self.assertEqual(aggregate_message(subjects, "feat", "main"),
                         "squash: feat → main")

    def test_single_subject_no_body(self):
        # 单条 → 仅 subject，无 body
        msg = aggregate_message(["feat: add login"], "feat", "main")
        self.assertEqual(msg, "feat: add login")

    def test_multi_subject_joined_with_body(self):
        msg = aggregate_message(["a", "b", "c"], "feat", "main")
        self.assertTrue(msg.startswith("a + b + c"))
        self.assertIn("\n\n", msg)
        self.assertIn("- a", msg)
        self.assertIn("- b", msg)
        self.assertIn("- c", msg)

    def test_dedup_preserves_order(self):
        msg = aggregate_message(["a", "b", "a", "c", "b"], "feat", "main")
        # 去重保序：a + b + c
        first_line = msg.splitlines()[0]
        self.assertEqual(first_line, "a + b + c")

    def test_filters_merge_noise_keeps_others(self):
        subjects = [
            "Merge branch 'main' into feat",
            "feat: real change",
            "Merge pull request #1",
            "fix: another",
        ]
        msg = aggregate_message(subjects, "feat", "main")
        first_line = msg.splitlines()[0]
        self.assertEqual(first_line, "feat: real change + fix: another")

    def test_strips_whitespace(self):
        msg = aggregate_message(["  a  ", ""], "feat", "main")
        self.assertEqual(msg, "a")

    def test_long_subject_truncated(self):
        long_subjects = [f"change{i}" * 10 for i in range(20)]
        msg = aggregate_message(long_subjects, "feat", "main")
        first_line = msg.splitlines()[0]
        self.assertLessEqual(len(first_line), 100)
        self.assertTrue(first_line.endswith("..."))

    def test_subject_with_arrow_not_confused(self):
        # 含 → 的 subject 不影响兜底文案拼接
        msg = aggregate_message(["a → b"], "feat", "main")
        self.assertEqual(msg, "a → b")


class TestParseMergeTreeOutput(unittest.TestCase):
    def test_no_conflict_just_tree_hash(self):
        out = "abc123\n"
        self.assertEqual(_parse_merge_tree_output(out), [])

    def test_conflict_name_only(self):
        out = (
            "abc123\n"
            "a.txt\n"
            "\n"
            "Auto-merging a.txt\n"
            "CONFLICT (content): Merge conflict in a.txt\n"
        )
        self.assertEqual(_parse_merge_tree_output(out), ["a.txt"])

    def test_conflict_multiple_files(self):
        out = (
            "abc123\n"
            "a.txt\n"
            "b.txt\n"
            "dir/c.txt\n"
        )
        self.assertEqual(_parse_merge_tree_output(out), ["a.txt", "b.txt", "dir/c.txt"])

    def test_index_lines_skipped(self):
        # 索引行格式 <mode> <sha> <stage>\t<path>
        out = (
            "abc123\n"
            "100644 78981922613b2afb6025042ff6bd878ac1994e85 1\ta.txt\n"
            "100644 655c6e645cc5a42912b35234205e82775c242252 2\ta.txt\n"
            "100644 162f064ced9ec755f4835f21ada43f0746001666 3\ta.txt\n"
        )
        # 索引行含 tab → 提取 path 部分，最终去重为 a.txt 一次（去重发生在调用方，parse 不去重）
        # parse 不去重，每行 tab 后 path 都提取出来
        self.assertEqual(_parse_merge_tree_output(out), ["a.txt", "a.txt", "a.txt"])


class TestDetectConflict(unittest.TestCase):
    """用真实 tmp git 仓库验证 detect_conflict 解析。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self._git_init(self.root)

    def tearDown(self):
        self._td.cleanup()

    @staticmethod
    def _git_init(root: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)

    def _commit(self, root: Path, msg: str) -> None:
        subprocess.run(["git", "commit", "-qm", msg], cwd=root, check=True)

    def test_no_conflict(self):
        # base + target 改不同文件 + source 改不同文件 → 无冲突
        (self.root / "base.txt").write_text("base")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        self._commit(self.root, "base")
        subprocess.run(["git", "checkout", "-qb", "target"], cwd=self.root, check=True)
        (self.root / "t.txt").write_text("t")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        self._commit(self.root, "target")
        subprocess.run(["git", "checkout", "-qb", "source", "master"],
                       cwd=self.root, check=True)
        (self.root / "s.txt").write_text("s")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        self._commit(self.root, "source")

        has, files = detect_conflict("source", "target", cwd=str(self.root))
        self.assertFalse(has)
        self.assertEqual(files, [])

    def test_real_conflict(self):
        # base + target 和 source 都改同一文件 → 冲突
        (self.root / "a.txt").write_text("base")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        self._commit(self.root, "base")
        subprocess.run(["git", "checkout", "-qb", "target"], cwd=self.root, check=True)
        (self.root / "a.txt").write_text("target_a")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        self._commit(self.root, "target")
        subprocess.run(["git", "checkout", "-qb", "source", "master"],
                       cwd=self.root, check=True)
        (self.root / "a.txt").write_text("source_a")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        self._commit(self.root, "source")

        has, files = detect_conflict("source", "target", cwd=str(self.root))
        self.assertTrue(has)
        self.assertIn("a.txt", files)


class TestRunSquashPrEndToEnd(unittest.TestCase):
    """临时仓库端到端：正常路径 + 4 异常路径 + 全量回滚。

    用 --no-prc 隔离掉 prc 的 AI 调用（测到 push 为止）。
    bare 远端模拟 origin。
    """

    def _make_repo(self) -> tuple[tempfile.TemporaryDirectory, str, str]:
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        # work 仓库 + bare origin
        work = root / "work"
        origin = root / "origin.git"
        work.mkdir()
        origin.mkdir()
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        subprocess.run(["git", "init", "-q"], cwd=work, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=work, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True)
        subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=work, check=True)
        # 默认分支 master，先提交 base
        (work / "base.txt").write_text("base")
        subprocess.run(["git", "add", "-A"], cwd=work, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=work, check=True)
        subprocess.run(["git", "branch", "-M", "master"], cwd=work, check=True)
        subprocess.run(["git", "push", "-q", "origin", "master"], cwd=work, check=True)
        return td, str(work), str(origin)

    def _chdir(self, work: str) -> None:
        """check_bit_clean/get_current_branch 无 cwd 参数，脚本须在仓库内运行。"""
        self._saved_cwd = os.getcwd()
        os.chdir(work)
        self.addCleanup(self._restore_cwd)

    def _restore_cwd(self) -> None:
        if hasattr(self, "_saved_cwd"):
            os.chdir(self._saved_cwd)

    def _git(self, work: str, *args: str) -> str:
        p = subprocess.run(["git", *args], cwd=work, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"git {args} failed: {p.stderr}")
        return p.stdout

    def _commit(self, work: str, msg: str, fname: str, content: str) -> None:
        Path(work, fname).write_text(content)
        subprocess.run(["git", "add", "-A"], cwd=work, check=True)
        subprocess.run(["git", "commit", "-qm", msg], cwd=work, check=True)

    def _build_source_target(self, work: str) -> str:
        """构造 source（含 merge commit + 噪声）+ target 分叉。返回 merge-base。"""
        # master 上已有 base commit
        # target 分支：从 master 分叉，加 t.txt
        self._git(work, "checkout", "-b", "target", "master")
        self._commit(work, "target: add t.txt", "t.txt", "t")
        self._git(work, "push", "-q", "origin", "target")
        # source 分支：从 master 分叉，多个 commit
        self._git(work, "checkout", "-b", "source", "master")
        self._commit(work, "feat: add login", "login.txt", "login")
        self._commit(work, "feat: add logout", "logout.txt", "logout")
        # 制造一个 merge commit（噪声）—— 把 target merge 进 source
        self._git(work, "merge", "--no-ff", "--no-edit", "-m",
                  "Merge branch 'target' into source", "target")
        self._git(work, "push", "-q", "origin", "source")
        # 回 master
        self._git(work, "checkout", "master")
        # 计算 merge-base
        mb = subprocess.run(["git", "merge-base", "source", "origin/target"],
                            cwd=work, capture_output=True, text=True, check=True).stdout.strip()
        return mb

    def test_normal_path_produces_single_commit_pr_branch(self):
        td, work, origin = self._make_repo()
        with td:
            self._chdir(work)
            mb = self._build_source_target(work)
            # 当前在 master；source / target 已推
            res = run_squash_pr("source", "target", no_prc=True,
                                r=MagicMock(), cwd=work)
            self.assertEqual(res.returncode, 0, f"应成功，实返回 {res.returncode}")
            self.assertEqual(res.merge_base, mb)
            # <source>_pr 远端分支存在
            ls = subprocess.run(
                ["git", "ls-remote", "--heads", origin, "source_pr"],
                capture_output=True, text=True,
            )
            self.assertEqual(ls.returncode, 0)
            self.assertIn("source_pr", ls.stdout)
            # 仅 1 commit
            log = subprocess.run(
                ["git", "log", "--oneline", f"{mb}..origin/source_pr"],
                cwd=work, capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(len(log.splitlines()), 1, f"应为单 commit: {log}")
            # 该 commit diff == git diff merge-base..source
            diff_pr = subprocess.run(
                ["git", "diff", f"{mb}..origin/source_pr"],
                cwd=work, capture_output=True, text=True, check=True,
            ).stdout
            diff_src = subprocess.run(
                ["git", "diff", f"{mb}..source"],
                cwd=work, capture_output=True, text=True, check=True,
            ).stdout
            self.assertEqual(diff_pr, diff_src)
            # source / target 历史未被改写
            src_sha = subprocess.run(
                ["git", "rev-parse", "source"], cwd=work,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            src_remote = subprocess.run(
                ["git", "rev-parse", "origin/source"], cwd=work,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(src_sha, src_remote)

    def test_dirty_workspace_aborts(self):
        td, work, origin = self._make_repo()
        with td:
            self._chdir(work)
            self._build_source_target(work)
            # 弄脏工作区
            Path(work, "dirty.txt").write_text("dirty")
            res = run_squash_pr("source", "target", no_prc=True,
                                r=MagicMock(), cwd=work)
            self.assertNotEqual(res.returncode, 0)
            # 没产生 source_pr 分支
            lb = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", "source_pr"],
                cwd=work, capture_output=True, text=True,
            )
            self.assertNotEqual(lb.returncode, 0)

    def test_source_behind_remote_aborts(self):
        td, work, origin = self._make_repo()
        with td:
            self._chdir(work)
            self._build_source_target(work)
            # 让本地 source 落后 origin/source：reset 本地 source 到 master
            self._git(work, "checkout", "source")
            self._git(work, "reset", "--hard", "master")
            self._git(work, "checkout", "master")
            res = run_squash_pr("source", "target", no_prc=True,
                                r=MagicMock(), cwd=work)
            self.assertNotEqual(res.returncode, 0)

    def test_merge_conflict_aborts_and_rolls_back(self):
        td, work, origin = self._make_repo()
        with td:
            self._chdir(work)
            # base commit
            # target 改 a.txt，source 改 a.txt → 冲突
            (Path(work) / "a.txt").write_text("base")
            subprocess.run(["git", "add", "-A"], cwd=work, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=work, check=True)
            # target
            self._git(work, "checkout", "-b", "target", "master")
            self._commit(work, "target", "a.txt", "target_a")
            self._git(work, "push", "-q", "origin", "target")
            # source
            self._git(work, "checkout", "-b", "source", "master")
            self._commit(work, "source", "a.txt", "source_a")
            self._git(work, "push", "-q", "origin", "source")
            self._git(work, "checkout", "master")

            orig_branch = subprocess.run(
                ["git", "branch", "--show-current"], cwd=work,
                capture_output=True, text=True, check=True,
            ).stdout.strip()

            res = run_squash_pr("source", "target", no_prc=True,
                                r=MagicMock(), cwd=work)
            self.assertNotEqual(res.returncode, 0)
            self.assertTrue(res.conflict_files, "应报告冲突文件")
            # 回滚：当前分支恢复为起始分支
            cur = subprocess.run(
                ["git", "branch", "--show-current"], cwd=work,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(cur, orig_branch)
            # source_pr 半成品分支被删
            lb = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", "source_pr"],
                cwd=work, capture_output=True, text=True,
            )
            self.assertNotEqual(lb.returncode, 0)

    def test_existing_pr_branch_aborts_without_force(self):
        td, work, origin = self._make_repo()
        with td:
            self._chdir(work)
            self._build_source_target(work)
            # 预先创建本地 source_pr 分支
            self._git(work, "branch", "source_pr", "master")
            # 非 TTY + 无 SQUASH_PR_FORCE_DELETE → 应中止
            with patch("lib.squash_pr_wf.sys.stdin") as mock_stdin, \
                 patch.dict("os.environ", {}, clear=False):
                mock_stdin.isatty.return_value = False
                os.environ.pop("SQUASH_PR_FORCE_DELETE", None)
                res = run_squash_pr("source", "target", no_prc=True,
                                    r=MagicMock(), cwd=work)
            self.assertNotEqual(res.returncode, 0)

    def test_existing_pr_branch_force_delete_recreates(self):
        td, work, origin = self._make_repo()
        with td:
            self._chdir(work)
            self._build_source_target(work)
            self._git(work, "branch", "source_pr", "master")
            mb = subprocess.run(
                ["git", "merge-base", "source", "origin/target"],
                cwd=work, capture_output=True, text=True, check=True,
            ).stdout.strip()
            with patch.dict("os.environ", {"SQUASH_PR_FORCE_DELETE": "1"}):
                res = run_squash_pr("source", "target", no_prc=True,
                                    r=MagicMock(), cwd=work)
            self.assertEqual(res.returncode, 0)
            self.assertEqual(res.merge_base, mb)
            # 仅 1 commit
            log = subprocess.run(
                ["git", "log", "--oneline", f"{mb}..origin/source_pr"],
                cwd=work, capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(len(log.splitlines()), 1)


class TestRunSquashPrCurrentBranchDefault(unittest.TestCase):
    """source 缺省时取当前分支的端到端验证（对应 bin/squash_pr <target> 形式）。"""

    def test_source_defaults_to_current_branch(self):
        td = tempfile.TemporaryDirectory()
        with td:
            root = Path(td.name)
            work = root / "work"
            origin = root / "origin.git"
            work.mkdir()
            origin.mkdir()
            subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
            subprocess.run(["git", "init", "-q"], cwd=work, check=True)
            subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=work, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True)
            subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=work, check=True)
            (work / "base.txt").write_text("base")
            subprocess.run(["git", "add", "-A"], cwd=work, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=work, check=True)
            subprocess.run(["git", "branch", "-M", "master"], cwd=work, check=True)
            subprocess.run(["git", "push", "-q", "origin", "master"], cwd=work, check=True)

            def _commit(msg, fname, content):
                (work / fname).write_text(content)
                subprocess.run(["git", "add", "-A"], cwd=work, check=True)
                subprocess.run(["git", "commit", "-qm", msg], cwd=work, check=True)

            subprocess.run(["git", "checkout", "-qb", "target", "master"], cwd=work, check=True)
            _commit("target", "t.txt", "t")
            subprocess.run(["git", "push", "-q", "origin", "target"], cwd=work, check=True)
            subprocess.run(["git", "checkout", "-qb", "mysource", "master"], cwd=work, check=True)
            _commit("feat: a", "a.txt", "a")
            _commit("feat: b", "b.txt", "b")
            subprocess.run(["git", "push", "-q", "origin", "mysource"], cwd=work, check=True)
            mb = subprocess.run(["git", "merge-base", "mysource", "origin/target"],
                                cwd=work, capture_output=True, text=True, check=True).stdout.strip()

            # 当前在 mysource → source 缺省应解析为 mysource
            # 模拟 bin/squash_pr <target>：source = 当前分支
            import os as _os
            saved = _os.getcwd()
            _os.chdir(str(work))
            try:
                # 模拟 bin/squash_pr <target>：source = 当前分支
                cur = subprocess.run(["git", "branch", "--show-current"],
                                     cwd=str(work), capture_output=True, text=True,
                                     check=True).stdout.strip()
                self.assertEqual(cur, "mysource")
                res = run_squash_pr(cur, "target", no_prc=True, r=MagicMock())
            finally:
                _os.chdir(saved)
            self.assertEqual(res.returncode, 0)
            self.assertEqual(res.merge_base, mb)
            log = subprocess.run(
                ["git", "log", "--oneline", f"{mb}..origin/mysource_pr"],
                cwd=str(work), capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(len(log.splitlines()), 1)


class TestAggregateIntegrationWithGit(unittest.TestCase):
    """用真实 git log 验证 aggregate_message 与 git 的集成。"""

    def test_log_format_subjects_pass_to_aggregate(self):
        td = tempfile.TemporaryDirectory()
        with td:
            root = Path(td.name)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
            (root / "a").write_text("a")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
            mb = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True, check=True).stdout.strip()
            for msg in ["Merge branch 'x'", "feat: a", "feat: b", "feat: a"]:  # 含噪声 + 重复
                (root / "a").write_text(msg)
                subprocess.run(["git", "add", "-A"], cwd=root, check=True)
                subprocess.run(["git", "commit", "-qm", msg], cwd=root, check=True)
            # 模拟 squash_pr 读 log 的方式
            log_p = subprocess.run(
                ["git", "log", "--no-merges", "--format=%s", f"{mb}..HEAD"],
                cwd=root, capture_output=True, text=True, check=True,
            )
            subjects = log_p.stdout.splitlines()
            # --no-merges 已跳 merge commit，但噪声 subject（非真 merge commit）仍在
            # 这里没真 merge commit，subjects = [feat: a, feat: b, feat: a]
            msg = aggregate_message(subjects, "source", "target")
            first_line = msg.splitlines()[0]
            self.assertEqual(first_line, "feat: a + feat: b")


if __name__ == "__main__":
    unittest.main()

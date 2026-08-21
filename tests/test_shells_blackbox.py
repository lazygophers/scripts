#!/usr/bin/env python3
"""薄壳黑盒冒烟测试（隔离环境）。

每个薄壳:
  - python3 bin/<name> --help / -h / --dry-run 应 SystemExit(0)
  - 在临时 HOME + cwd 下运行, 不污染用户环境
  - 不实际触发 git/claude/say 等外部副作用

覆盖: bin/ 下所有可执行薄壳的 import 链 + 通用参数配置正确性。
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "bin"


def _all_shells() -> list[str]:
    """返回 bin/ 下所有可执行文件名（排除目录）。"""
    shells = []
    for p in sorted(BIN_DIR.iterdir()):
        if p.is_file() and os.access(p, X_OK := os.R_OK | os.W_OK | os.X_OK):
            shells.append(p.name)
    return shells


# bash-only 入口（fire/python 测试套不适用；sys.executable 跑会 SyntaxError）。
# 单独覆盖在 test_disable_ipv6_runs_in_bash 类。
_BASH_ONLY_BINS = {"disable-ipv6"}


class TestShellCommonFlags(unittest.TestCase):
    """所有薄壳通用参数必须正常退出。"""

    def _run_shell(self, name: str, flag: str) -> subprocess.CompletedProcess:
        # 隔离: 临时 HOME 防止任何 rc 副作用; PYTHONPATH 指向 repo root
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": tempfile.mkdtemp(prefix="shelltest_home_"),
            "PYTHONPATH": str(REPO_ROOT),
            "LC_ALL": "en_US.UTF-8",
            "TERM": "dumb",
        }
        cwd = tempfile.mkdtemp(prefix="shelltest_cwd_")
        return subprocess.run(
            [sys.executable, str(BIN_DIR / name), flag],
            capture_output=True, text=True, env=env, cwd=cwd, timeout=10,
        )

    def test_all_shells_common_flags_exit_zero(self):
        shells = [s for s in _all_shells() if s not in _BASH_ONLY_BINS]
        self.assertGreater(len(shells), 10, "应检测到多个薄壳")
        failures = []
        for name in shells:
            for flag in ("--help", "-h", "--dry-run"):
                with self.subTest(shell=name, flag=flag):
                    p = self._run_shell(name, flag)
                    if p.returncode != 0:
                        failures.append((name, flag, p.returncode, p.stderr[:200]))
        if failures:
            msg = "\n".join(f"{n} {flag}: exit={rc} stderr={e}" for n, flag, rc, e in failures)
            self.fail(f"薄壳通用参数失败:\n{msg}")


class TestInjectDryRunIsolated(unittest.TestCase):
    """inject show 不写盘 (隔离 HOME 验证)。fire 重构后用 `inject show` 子命令。"""

    def test_show_does_not_write(self):
        home = tempfile.mkdtemp(prefix="inject_home_")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": home,
            "PYTHONPATH": str(REPO_ROOT),
        }
        p = subprocess.run(
            [sys.executable, str(BIN_DIR / "inject"), "show"],
            capture_output=True, text=True, env=env, timeout=10,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        # 验证 scripts.sh 未被写入用户 HOME
        target = Path(home) / ".config" / "lazygophers" / "scripts" / "scripts.sh"
        self.assertFalse(target.exists(), "inject show 不应写盘")


class TestNRejectsDangerousIsolated(unittest.TestCase):
    """bin/n 拒绝危险字符 (不实际 say)。fire 重构后用 `n say <content>`。"""

    def test_rejects_semicolon(self):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": tempfile.mkdtemp(),
               "PYTHONPATH": str(REPO_ROOT)}
        p = subprocess.run(
            [sys.executable, str(BIN_DIR / "n"), "say", "msg; rm -rf /"],
            capture_output=True, text=True, env=env, timeout=10,
        )
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_rejects_overlong(self):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": tempfile.mkdtemp(),
               "PYTHONPATH": str(REPO_ROOT)}
        p = subprocess.run(
            [sys.executable, str(BIN_DIR / "n"), "say", "x" * 501],
            capture_output=True, text=True, env=env, timeout=10,
        )
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)


class TestBatchGitCliBlackbox(unittest.TestCase):
    """批量 Git CLI 黑盒：系统临时目录 + 本地 bare remote。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="batch_git_cli_")
        self.root = Path(self.tmp.name) / "work"
        self.root.mkdir()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.remote = Path(self.tmp.name) / "remote.git"
        self.repo = self.root / "repo1"
        self.env = {
            "PATH": f"{BIN_DIR}:{os.environ.get('PATH', '')}",
            "HOME": str(self.home),
            "PYTHONPATH": str(REPO_ROOT),
            "SCRIPTS_NO_SAY": "1",
            "BATCH_NO_CONFIRM": "1",
            "TERM": "dumb",
        }
        self._git(None, "init", "--bare", "--initial-branch=main", str(self.remote))
        self._git(None, "init", "--initial-branch=main", str(self.repo))
        self._git(self.repo, "config", "user.email", "test@example.invalid")
        self._git(self.repo, "config", "user.name", "Test User")
        self._git(self.repo, "remote", "add", "origin", str(self.remote))
        (self.repo / "README.md").write_text("main\n")
        self._git(self.repo, "add", "README.md")
        self._git(self.repo, "commit", "-m", "init")
        self._git(self.repo, "push", "-u", "origin", "main")
        self._git(self.repo, "fetch", "origin")

    def tearDown(self):
        self.tmp.cleanup()

    def _git(self, cwd: Path | None, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=cwd, env=self.env, text=True,
            capture_output=True, check=True, timeout=20,
        )

    def _run_bin(self, name: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN_DIR / name), *args], cwd=self.root, env=self.env,
            text=True, capture_output=True, timeout=30,
        )

    def _branch(self) -> str:
        return self._git(self.repo, "branch", "--show-current").stdout.strip()

    def test_switch_branch_batch_creates_from_origin_main(self):
        p = self._run_bin("switch_branch", "to", "topic")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self._branch(), "topic")
        main_sha = self._git(self.repo, "rev-parse", "main").stdout.strip()
        topic_sha = self._git(self.repo, "rev-parse", "topic").stdout.strip()
        self.assertEqual(topic_sha, main_sha)

    def test_delete_branch_batch_only_deletes_test_branch(self):
        self._git(self.repo, "switch", "-c", "delete-me")
        self._git(self.repo, "switch", "main")
        p = self._run_bin("delete_branch", "all", "delete-me", "-y")
        self.assertEqual(p.returncode, 0, p.stderr)
        branches = self._git(self.repo, "branch", "--list").stdout
        self.assertNotIn("delete-me", branches)
        self.assertIn("main", branches)
        self.assertEqual(self._branch(), "main")

    def test_delete_branch_remote_batch_only_deletes_test_branch(self):
        self._git(self.repo, "switch", "-c", "remote-delete-me")
        (self.repo / "remote.txt").write_text("remote\n")
        self._git(self.repo, "add", "remote.txt")
        self._git(self.repo, "commit", "-m", "remote branch")
        self._git(self.repo, "push", "-u", "origin", "remote-delete-me")
        self._git(self.repo, "switch", "main")
        p = self._run_bin("delete_branch_remote", "all", "remote-delete-me", "-y")
        self.assertEqual(p.returncode, 0, p.stderr)
        remote_heads = self._git(None, "--git-dir", str(self.remote), "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout
        self.assertIn("main", remote_heads)
        self.assertNotIn("remote-delete-me", remote_heads)

    def test_push_branch_batch_pushes_current_test_branch(self):
        self._git(self.repo, "switch", "-c", "push-me")
        (self.repo / "push.txt").write_text("push\n")
        self._git(self.repo, "add", "push.txt")
        self._git(self.repo, "commit", "-m", "push branch")
        p = self._run_bin("push_branch", "current")
        self.assertEqual(p.returncode, 0, p.stderr)
        remote_heads = self._git(None, "--git-dir", str(self.remote), "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout
        self.assertIn("push-me", remote_heads)
        self.assertIn("main", remote_heads)

    def test_push_branch_batch_creates_missing_named_branch(self):
        p = self._run_bin("push_branch", "to", "test")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self._branch(), "test")
        self.assertEqual(
            self._git(self.repo, "rev-parse", "test").stdout.strip(),
            self._git(self.repo, "rev-parse", "main").stdout.strip(),
        )
        remote_heads = self._git(None, "--git-dir", str(self.remote), "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout
        self.assertIn("test", remote_heads)


if __name__ == "__main__":
    unittest.main()

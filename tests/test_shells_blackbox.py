#!/usr/bin/env python3
"""薄壳黑盒冒烟测试（隔离环境）。

每个薄壳:
  - python3 bin/<name> --help 应 SystemExit(0)
  - 在临时 HOME + cwd 下运行, 不污染用户环境
  - 不实际触发 git/claude/say 等外部副作用 (--help 仅走 argparse)

覆盖: bin/ 下所有可执行薄壳的 import 链 + argparse 配置正确性。
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
    """返回 bin/ 下所有可执行文件名（排除 _ 前缀的内部脚本 + inject 等有副作用的）。"""
    shells = []
    for p in sorted(BIN_DIR.iterdir()):
        if p.name.startswith("_"):
            continue
        if p.is_file() and os.access(p, os.X_OK):
            shells.append(p.name)
    return shells


class TestShellHelp(unittest.TestCase):
    """所有薄壳 --help 必须正常退出 (import 链 + argparse 通)。"""

    def _run_help(self, name: str) -> subprocess.CompletedProcess:
        # 隔离: 临时 HOME 防止任何 rc 副作用; PYTHONPATH 指向 repo root
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": tempfile.mkdtemp(prefix="shelltest_home_"),
            "PYTHONPATH": str(REPO_ROOT),
            "LC_ALL": "en_US.UTF-8",
            # 关闭可能干扰的交互式 env
            "TERM": "dumb",
        }
        # 在隔离 cwd 下运行
        cwd = tempfile.mkdtemp(prefix="shelltest_cwd_")
        return subprocess.run(
            [sys.executable, str(BIN_DIR / name), "--help"],
            capture_output=True, text=True, env=env, cwd=cwd, timeout=10,
        )

    def test_all_shells_help_exit_zero(self):
        shells = _all_shells()
        self.assertGreater(len(shells), 10, "应检测到多个薄壳")
        failures = []
        for name in shells:
            with self.subTest(shell=name):
                p = self._run_help(name)
                # argparse --help → SystemExit(0); 但部分薄壳无 argparse (如 cpd)
                # 接受 0; 其他码记录失败
                if p.returncode != 0:
                    failures.append((name, p.returncode, p.stderr[:200]))
        if failures:
            msg = "\n".join(f"{n}: exit={rc} stderr={e}" for n, rc, e in failures)
            self.fail(f"薄壳 --help 失败:\n{msg}")


class TestInjectDryRunIsolated(unittest.TestCase):
    """inject --show 不写盘 (隔离 HOME 验证)。"""

    def test_show_does_not_write(self):
        home = tempfile.mkdtemp(prefix="inject_home_")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": home,
            "PYTHONPATH": str(REPO_ROOT),
        }
        p = subprocess.run(
            [sys.executable, str(BIN_DIR / "inject"), "--show"],
            capture_output=True, text=True, env=env, timeout=10,
        )
        self.assertEqual(p.returncode, 0)
        # 验证 scripts.sh 未被写入用户 HOME
        target = Path(home) / ".config" / "lazygophers" / "scripts" / "scripts.sh"
        self.assertFalse(target.exists(), "inject --show 不应写盘")


class TestNRejectsDangerousIsolated(unittest.TestCase):
    """bin/n 拒绝危险字符 (不实际 say)。"""

    def test_rejects_semicolon(self):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": tempfile.mkdtemp(),
               "PYTHONPATH": str(REPO_ROOT)}
        p = subprocess.run(
            [sys.executable, str(BIN_DIR / "n"), "msg; rm -rf /"],
            capture_output=True, text=True, env=env, timeout=10,
        )
        self.assertEqual(p.returncode, 1)

    def test_rejects_overlong(self):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": tempfile.mkdtemp(),
               "PYTHONPATH": str(REPO_ROOT)}
        p = subprocess.run(
            [sys.executable, str(BIN_DIR / "n"), "x" * 501],
            capture_output=True, text=True, env=env, timeout=10,
        )
        self.assertEqual(p.returncode, 1)


if __name__ == "__main__":
    unittest.main()

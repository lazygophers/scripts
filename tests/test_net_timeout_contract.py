#!/usr/bin/env python3
"""网络 git 命令必须带 timeout —— AST 契约测试。

回归背景（2026-09）：switch_branch 批量 14 仓卡在「检测中 0%」2 分半不动。
根因：detect 的 `git fetch origin` 走 run() 不传 timeout，远端慢/需认证时
fetch 永不返回，并发 4 个 worker 全挂在 fetch 上，整批无输出挂死。
push_*/merge_* 系列同构暴露。

修法不是逐处补 timeout，是让本测试锁死契约：lib/ 下任何
run / _run / _git / run_logged / retry_command 调用，只要命令是网络 git
子命令（fetch / push / pull / ls-remote / clone），就必须传 timeout=。
新增代码漏传时本测试直接红，禁止回归。
"""
import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "lib"

# 包装 run() 的执行器们（lib 内调用网络 git 的全部入口）
_RUNNERS = {"run", "_run", "_git", "run_logged", "retry_command"}
# 会碰网络的 git 子命令
_NET_SUBCOMMANDS = {"fetch", "push", "pull", "ls-remote", "clone"}


def _iter_net_git_calls(tree):
    """产出 (file, lineno, 函数名, cmd) —— 所有网络 git 调用点。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name not in _RUNNERS or not node.args:
            continue
        first = node.args[0]
        # 取第一个元素字面量：["git", "fetch", ...] 或 ["fetch", ...]（_git 风格）
        if not (isinstance(first, ast.List) and first.elts
                and isinstance(first.elts[0], ast.Constant)
                and isinstance(first.elts[0].value, str)):
            continue
        tokens = [e.value for e in first.elts
                  if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        sub = None
        if tokens and tokens[0] == "git" and len(tokens) > 1:
            sub = tokens[1]
        elif tokens and tokens[0] in _NET_SUBCOMMANDS:
            sub = tokens[0]
        if sub in _NET_SUBCOMMANDS:
            yield node, name, sub


class TestNetGitTimeoutContract(unittest.TestCase):
    def test_all_net_git_calls_have_timeout(self) -> None:
        violations = []
        checked = 0
        for py in sorted(LIB_DIR.glob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node, runner, sub in _iter_net_git_calls(tree):
                checked += 1
                has_timeout = any(
                    kw.arg == "timeout" and
                    not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
                    for kw in node.keywords
                )
                if not has_timeout:
                    violations.append(f"{py.name}:{node.lineno}: {runner}(... {sub} ...) 缺 timeout")
        self.assertGreater(checked, 5, f"扫描到的网络 git 调用过少（{checked}），扫描器可能失效")
        self.assertFalse(violations, "网络 git 命令缺 timeout:\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()

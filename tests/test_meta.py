#!/usr/bin/env python3
"""测试套件自身的守卫：写了但从没跑过的测试等于没有测试。

起因（2026-09-25）：tests/test_kk.py 的三个类是 pytest 风格（没继承
unittest.TestCase），`python3 -m unittest discover` 静默收集到 0 个，
18 个用例躺了很久没人跑。这里让这种情况直接失败。
"""
from __future__ import annotations

import contextlib
import io
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

TEST_DEF_RE = re.compile(r"^\s*def (test_\w+)", re.M)


def _count(suite) -> int:
    if isinstance(suite, unittest.TestSuite):
        return sum(_count(t) for t in suite)
    return 1


class TestEveryTestIsCollected(unittest.TestCase):
    """tests/ 下每个 test_ 方法都必须被 unittest 收集到。"""

    def test_no_uncollected_tests(self):
        loader = unittest.TestLoader()
        missing = []
        for path in sorted(TESTS.glob("test_*.py")):
            if path.name == Path(__file__).name:
                continue
            declared = len(set(TEST_DEF_RE.findall(path.read_text(encoding="utf-8"))))
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                loaded = _count(loader.loadTestsFromName(f"tests.{path.stem}"))
            if loaded < declared:
                missing.append(f"{path.name}: 写了 {declared} 个，只收集到 {loaded} 个")
        self.assertEqual(
            missing, [],
            "有测试没被 unittest 收集（多半是类没继承 unittest.TestCase）:\n"
            + "\n".join(missing),
        )


class TestEveryBinIsInstallable(unittest.TestCase):
    """bin/<name> 必须同时在 pyproject 的 [project.scripts] 里注册。

    只有 clone 仓库的人能跑 bin/<name>；uvx 用户走 [project.scripts]。
    漏注册 = 这个工具对 uvx 用户不存在。
    """

    def test_every_bin_has_a_console_script(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        scripts_block = pyproject.split("[project.scripts]", 1)[1].split("\n[", 1)[0]
        registered = set(re.findall(r"^(\w[\w_-]*) = ", scripts_block, re.M))
        shells = {p.name for p in (ROOT / "bin").iterdir() if p.is_file()}
        self.assertEqual(
            sorted(shells - registered), [],
            "这些 bin/ 薄壳没在 pyproject [project.scripts] 注册，uvx 用户用不到",
        )


if __name__ == "__main__":
    unittest.main()

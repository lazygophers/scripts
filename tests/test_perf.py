#!/usr/bin/env python3
"""性能回归守卫：启动开销与导入图。

这些 CLI 是交互式用的，启动开销直接是等待时间；测试套件里又有 180 次薄壳
进程启动，启动慢一倍整套就慢一倍。这里守两件事：

1. 导入图（确定性，不看时钟）：git 类命令不许把 requests / HTTP 栈拖进来。
   实测 `import requests` 单独就要约 200ms。
2. 墙钟（松阈值，只拦灾难）：`bin/<name> --help` 的启动时间。阈值取实测
   中位数的 8 倍以上，CI 机器慢或并行跑满也不该误报；真正要拦的是
   「有人在 lib/fire_base.py 顶上加了一行 import requests」这种。
"""
from __future__ import annotations

import statistics
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 不碰网络的命令：HTTP 栈不该出现在它们的导入图里
NO_HTTP_CLIS = ["list_branch", "kk", "kkp", "cpd", "switch_branch", "sync_branch", "gitwf"]
HTTP_MODULES = ["requests", "urllib3", "email.parser"]

STARTUP_BUDGET_SECONDS = 3.0


def _import_snapshot(module: str) -> set[str]:
    """子进程里 import 目标模块，回报 sys.modules 的键集合。"""
    code = f"import sys; import {module}; print('\\n'.join(sorted(sys.modules)))"
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise AssertionError(f"import {module} 失败: {p.stderr[-500:]}")
    return set(p.stdout.split())


class TestImportBudget(unittest.TestCase):
    def test_git_clis_do_not_import_http_stack(self):
        with ThreadPoolExecutor(max_workers=len(NO_HTTP_CLIS)) as pool:
            snapshots = dict(zip(
                NO_HTTP_CLIS,
                pool.map(lambda c: _import_snapshot(f"lib.cli.{c}"), NO_HTTP_CLIS),
            ))
        offenders = {cli: hit for cli, mods in snapshots.items()
                     if (hit := sorted(m for m in HTTP_MODULES if m in mods))}
        self.assertEqual(offenders, {},
                         "这些命令不碰网络，却把 HTTP 栈拖进了导入图（每个约 200ms）")

    def test_reporter_does_not_need_a_console_before_printing(self):
        """lib.ui 本身能 import 成功且不启动任何子进程/网络（基线健康检查）。"""
        mods = _import_snapshot("lib.ui")
        self.assertIn("rich.console", mods)
        self.assertNotIn("requests", mods)


class TestStartupBudget(unittest.TestCase):
    def _median_startup(self, name: str, runs: int = 3) -> float:
        samples = []
        for _ in range(runs):
            started = time.perf_counter()
            subprocess.run([sys.executable, str(ROOT / "bin" / name), "--help"],
                           capture_output=True, timeout=120)
            samples.append(time.perf_counter() - started)
        return statistics.median(samples)

    def test_help_starts_within_budget(self):
        names = ("cpd", "list_branch", "kk")
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            medians = dict(zip(names, pool.map(self._median_startup, names)))
        slow = {n: round(m, 2) for n, m in medians.items() if m > STARTUP_BUDGET_SECONDS}
        self.assertEqual(slow, {},
                         f"--help 启动超过 {STARTUP_BUDGET_SECONDS}s（单位秒）")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""性能回归守卫：启动开销与导入图。

这些 CLI 是交互式用的，启动开销直接是等待时间；测试套件里又有 180 次薄壳
进程启动，启动慢一倍整套就慢一倍。这里守两件事：

1. 导入图（确定性，不看时钟）：git 类命令不许把 requests / HTTP 栈拖进来。
   实测 `import requests` 单独就要约 200ms。
2. 启动开销（确定性，不掐表）：一条命令启动要 import 多少个模块。模块数是
   启动时间的来源，又不受机器负载影响；真正要拦的是「有人在
   lib/fire_base.py 顶上加了一行 import requests」。
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 不碰网络的命令：HTTP 栈不该出现在它们的导入图里
NO_HTTP_CLIS = ["list_branch", "kk", "kkp", "cpd", "switch_branch", "sync_branch", "gitwf"]
# ssl 单独值 ~15ms 且只被 asyncio 间接拖进来——fire 的惰性 asyncio 占位
# （lib/fire_base.py）只护住 fire 这条导入路径，别的公共模块顶层照样能把它拖回来
HTTP_MODULES = ["requests", "urllib3", "email.parser", "ssl"]



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

    def test_fire_path_defers_asyncio(self):
        """fire.core 顶层 import asyncio 只服务交互 REPL，却在每个进程拖进
        ssl/socket/logging 约 27ms；lib.fire_base 塞惰性占位挡住它。守两条：
        fire 路径不加载 ssl；占位代理真能透明换成真 asyncio。"""
        mods = _import_snapshot("lib.fire_base")
        self.assertNotIn("ssl", mods)
        code = (
            "import lib.fire_base, sys, asyncio\n"
            "print(callable(asyncio.run))\n"
            "print(type(sys.modules['asyncio']).__name__)\n"
        )
        p = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        self.assertEqual(p.stdout.split(), ["True", "module"])

    def test_reporter_does_not_need_a_console_before_printing(self):
        """lib.ui import 成功、不启动子进程/网络，且 rich 不在导入图里
        （2026-10-05 起 rich 惰性加载：首次构造 Console/渲染才 _load_rich()，
        整链约 14ms，纯函数路径不该付这笔钱）。"""
        mods = _import_snapshot("lib.ui")
        self.assertNotIn("rich.console", mods)
        self.assertNotIn("requests", mods)


class TestStartupBudget(unittest.TestCase):
    """启动开销用「导入了多少个模块」衡量，不掐表。

    掐表的版本试过两次都不成立：绝对秒数在满载机器上假失败（本套件自己并行
    跑时 list_branch 3.01s 撞线 3.0s），改成「相对裸解释器的倍数」后同一台
    空载机上实测在 5.8~13.6 倍之间飘，阈值只能放到没有拦截力的位置。

    模块数是确定性的，而且正是启动时间的来源：启动慢就是因为 import 多。
    上限取实测值 +15%，够拦「有人在公共模块顶上加了一个重依赖」。
    """

    # 2026-10-05 两轮惰性化后实测：fire 路径 asyncio 没了、rich 走 _load_rich()
    # 按需加载，cpd 181 / list_branch 148 / kk 209 / browse 201，预算 = 实测 +15%
    MODULE_BUDGET = {"cpd": 208, "list_branch": 170, "kk": 240, "browse": 231}

    def _module_count(self, cli: str) -> int:
        code = f"import sys; import lib.cli.{cli}; print(len(sys.modules))"
        p = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        return int(p.stdout.strip())

    def test_startup_imports_stay_within_budget(self):
        with ThreadPoolExecutor(max_workers=len(self.MODULE_BUDGET)) as pool:
            counts = dict(zip(self.MODULE_BUDGET, pool.map(self._module_count, self.MODULE_BUDGET)))
        over = {cli: count for cli, count in counts.items() if count > self.MODULE_BUDGET[cli]}
        self.assertEqual(over, {}, f"启动导入的模块数超预算（预算 {self.MODULE_BUDGET}）")

    def test_fire_help_does_not_load_ipython(self):
        """fire 的 help 路径默认 `from IPython.core import oinspect`（+126ms、
        IPython 全家进内存）；run_cli 塞 sys.modules["IPython"]=None 让它回落
        _InfoBackup。这里守的是那行 setdefault 不被删——删了 help 输出不变，
        但每条 fire 命令的 --help 都静默慢 3 倍。"""
        code = (
            "import sys; sys.argv = ['n', '--help'];\n"
            "from lib.fire_base import run_cli\n"
            "class C:\n"
            "    def x(self): return 0\n"
            "try:\n"
            "    run_cli(C())\n"
            "except SystemExit:\n"
            "    pass\n"
            "print('IPY' if 'IPython.core' in sys.modules else 'OK')\n"
        )
        p = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        self.assertIn("OK", p.stdout.split())


if __name__ == "__main__":
    unittest.main()

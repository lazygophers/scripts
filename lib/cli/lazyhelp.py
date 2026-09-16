"""lazyhelp — 一页速查所有 bin/ 工具及功能（fire 重构）

默认无参数：按分类速查全部 bin/ 工具。
任意参数 <name>：调 bin/<name> --help，剩余参数透传。
"""
from __future__ import annotations

import sys

from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.lazyhelp import _all_bins, _render_table, show_full


class LazyhelpCli(BaseCli):
    """一页速查所有 bin/ 工具及功能"""

    @timed_cli
    def all(self):
        """默认：按分类速查全部 bin/ 工具及功能"""
        from lib.lazyhelp import TOOLS
        rows = [(name, cat, desc) for name, (cat, desc) in sorted(TOOLS.items())]
        self._r.rule(f"bin/ 工具速查（共 {len(rows)} 个）", style="blue")
        self._r.step("用法: lazyhelp help <工具名>  # 输出该工具的完整 --help")
        _render_table(rows, self._r)
        return 0

    @timed_cli
    def help(self, name: str, *extra: str):
        """调 bin/<name> --help 输出完整说明

        用法: lazyhelp help <工具名> [额外参数...]
        """
        # 把所有位置参数都透传给目标 bin
        return show_full(name, extra_args=list(extra))

    @timed_cli
    def list(self):
        """按字母排序输出所有 bin/ 工具名"""
        names = _all_bins()
        for n in names:
            print(n)
        return 0

    @timed_cli
    def install(self, yes: bool = False):
        """一次性跑完 browse + graphwatch 各自的完整安装流程

        用法: lazyhelp install [-y]

        每个都单独确认一次，选了才装；`-y`/`--yes` 跳过确认，全部默认同意。
        两边各自独立：一边失败/跳过不拦另一边，最后按「有一个真失败就非零」汇总
        退出码（跳过不算失败）。
        """
        from lib.browse_install import main as browse_install_main
        from lib.graphwatch import GraphwatchCli
        from lib.ui import ask_confirm

        failed = False

        if yes or ask_confirm("装 browse（浏览器扩展 native messaging）？", default=True):
            self._r.rule("browse install", style="blue")
            if browse_install_main(["browse install"]):
                failed = True
        else:
            self._r.step("跳过 browse install")

        if yes or ask_confirm("装 graphwatch（知识图谱后台服务）？", default=True):
            self._r.rule("graphwatch install", style="blue")
            if GraphwatchCli().install():
                failed = True
        else:
            self._r.step("跳过 graphwatch install")

        return 1 if failed else 0


def main():
    # 默认行为：fire 把类方法当 subcommand；无 subcommand 时 fire 默认打印总览，
    # 但我们要的是直接渲染速查表。用 Fire 的 trace 拦截太重，直接判 argv。
    if len(sys.argv) <= 1:
        # 无参数 → 直接调 all() 并退出（避免 fire 走总览打印）
        from lib.lazyhelp import TOOLS
        r = LazyhelpCli()._r
        rows = [(name, cat, desc) for name, (cat, desc) in sorted(TOOLS.items())]
        r.rule(f"bin/ 工具速查（共 {len(rows)} 个）", style="blue")
        r.step("用法: lazyhelp help <工具名>  # 输出该工具的完整 --help")
        _render_table(rows, r)
        sys.exit(0)
    run_cli(LazyhelpCli())

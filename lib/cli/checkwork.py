"""checkwork — 多语言编译检查（fire 重构）"""
from __future__ import annotations

from lib.build import run_checkwork
from lib.fire_base import BaseCli, run_cli, timed_cli


class CheckworkCli(BaseCli):
    """多语言编译检查（Go/Rust/Python/Java/Node）"""

    def __call__(self):
        """裸调用 `checkwork` 等同 `checkwork run`"""
        return self.run()

    @timed_cli
    def run(self):
        """执行当前目录（单仓或批量）的编译检查"""
        return run_checkwork()


def main():
    run_cli(CheckworkCli())

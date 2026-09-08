"""kkp — 按端口号终止占用进程（fire 重构）"""
from __future__ import annotations

import os
import sys

from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.process import kill_by_port


class KkpCli(BaseCli):
    """按端口号终止占用进程"""

    def __call__(self, *args: str, dry_run: bool = False):
        """裸调用 `kkp <port>` 等同 `kkp by_port <port>`"""
        if not args:
            self._r.err("kkp: 缺少端口号")
            return 1
        return self.by_port(int(args[0]), dry_run=dry_run)

    @timed_cli
    def by_port(self, port: int, dry_run: bool = False):
        """终止占用指定端口的进程

        用法: kkp <port> [--dry-run]
        """
        return kill_by_port(
            str(port),
            dry_run=dry_run,
            script_markers={os.path.basename(sys.argv[0]), "kkp"},
        )


def main():
    run_cli(KkpCli())

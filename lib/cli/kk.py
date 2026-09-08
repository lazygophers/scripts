"""kk — 按进程名终止进程（fire 重构）"""
from __future__ import annotations

import pathlib
import sys

from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.process import kill_by_name


class KkCli(BaseCli):
    """按进程名终止进程（正则）"""

    def __call__(self, *patterns: str, dry_run: bool = False):
        """裸调用 `kk <pattern...>` 等同 `kk by_name <pattern...>`"""
        return self.by_name(*patterns, dry_run=dry_run)

    @timed_cli
    def by_name(self, *patterns: str, dry_run: bool = False):
        """终止匹配的进程

        用法: kk <pattern1> [pattern2 ...] [--dry-run]
        """
        if not patterns:
            self._r.err("kk: 至少需要一个进程名")
            return 1
        return kill_by_name(
            list(patterns),
            dry_run=dry_run,
            script_markers={pathlib.Path(sys.argv[0]).name, "kk"},
        )


def main():
    run_cli(KkCli())

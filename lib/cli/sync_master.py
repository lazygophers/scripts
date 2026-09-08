"""sync_master — 批量同步主分支（master/main 自动识别）（fire 重构）"""
from __future__ import annotations

from lib.batch_git import sync_master_all
from lib.fire_base import BaseCli, run_cli, timed_cli


class SyncMasterCli(BaseCli):
    """批量同步主分支（master/main 自动识别）"""

    def __call__(self, force: bool = False):
        """裸调用 `sync_master` 等同 `sync_master run`"""
        return self.run(force=force)

    @timed_cli
    def run(self, force: bool = False):
        """同步主分支到 origin/<主分支>

        用法: sync_master run [--force]
        """
        return sync_master_all(force=force)


def main():
    run_cli(SyncMasterCli())

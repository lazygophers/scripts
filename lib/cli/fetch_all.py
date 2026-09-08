"""fetch_all — 一键拉取所有仓库远程更新（fire 重构）"""
from __future__ import annotations

from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.git import fetch_all


class FetchAllCli(BaseCli):
    """一键拉取所有仓库远程更新"""

    def __call__(self):
        """裸调用 `fetch_all` 等同 `fetch_all all`"""
        return self.all()

    @timed_cli
    def all(self):
        """扫描顶层 Git 仓库并 fetch 全部"""
        return fetch_all()


def main():
    run_cli(FetchAllCli())

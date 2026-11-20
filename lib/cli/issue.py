"""issue — 自动创建 Issue（LAZYGOPHERS API 生成 title/body）（fire 重构）"""
from __future__ import annotations

from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.issue_wf import run_issue


class IssueCli(BaseCli):
    """自动创建 Issue（LAZYGOPHERS API 生成 title/body）"""

    def __call__(
        self,
        *args: str,
        dry_run: bool = False,
        labels: str | None = None,
        assignee: str | None = None,
        milestone: str | None = None,
    ):
        """裸调用 `issue [<title>]` 等同 `issue create [<title>]`"""
        title = " ".join(args) if args else None
        return self.create(
            title, dry_run=dry_run,
            labels=labels, assignee=assignee, milestone=milestone,
        )

    @timed_cli
    def create(
        self,
        title: str | None = None,
        *,
        dry_run: bool = False,
        labels: str | None = None,
        assignee: str | None = None,
        milestone: str | None = None,
    ):
        """创建 Issue

        用法: issue create [<title>] [--labels ...] [--assignee ...] [--milestone ...] [--dry-run]
        """
        return run_issue(
            title,
            dry_run=dry_run,
            labels=labels,
            assignee=assignee,
            milestone=milestone,
        )


def main():
    run_cli(IssueCli())

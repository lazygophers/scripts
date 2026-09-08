"""squash_pr — 把当前分支自分叉以来的改动压成单 commit → 对接 mr 开 PR（fire 重构）

source 恒为当前分支：这条命令就是在源分支上跑的。
产出一个仅含单 commit 的 PR 分支（默认 <当前分支>_pr，可用第二个参数改名），
push 后调 mr <target> 开 PR。当前分支与 target 分支本身不动。
"""
from __future__ import annotations

from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.git import get_current_branch
from lib.squash_pr_wf import run_squash_pr


class SquashPrCli(BaseCli):
    """压当前分支自分叉以来的改动为单 commit → 开 PR"""

    def __call__(
        self,
        *args: str,
        pr_branch: str | None = None,
        dry_run: bool = False,
        push_only: bool = False,
    ):
        """裸调用 `squash_pr <target> [pr_branch]` 等同 `squash_pr run <target> [pr_branch]`

        args: 第一项 target；第二项是 PR 分支名（亦可用 --pr-branch=）。"""
        if not args:
            self._r.err("squash_pr: 缺少 target 分支名")
            return 1
        target = args[0]
        if len(args) >= 2:
            pr_branch = args[1]
        return self.run(target, pr_branch, dry_run=dry_run, push_only=push_only)

    @timed_cli
    def run(
        self,
        target: str,
        pr_branch: str | None = None,
        *,
        dry_run: bool = False,
        push_only: bool = False,
    ):
        """执行 squash → push → 开 PR

        用法: squash_pr run <target> [pr_branch] [--dry-run] [--push-only]

        pr_branch 省略时用 <当前分支>_pr；传了就用它承载单 commit，
        不存在则新建，已存在则重置到当前分支后 force push（保持同一 PR）。
        """
        source = get_current_branch()
        if not source:
            self._r.err("squash_pr: 无法获取当前分支（detached HEAD？）")
            return 1
        res = run_squash_pr(source, target, pr_branch=pr_branch,
                            dry_run=dry_run, no_mr=push_only)
        return res.returncode


def main():
    run_cli(SquashPrCli())

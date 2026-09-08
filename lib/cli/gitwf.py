"""merge*/push* 系列的统一实现（fire）

每个入口把 action 与 target 分支显式传进来:
  merge_canary/merge_dev/merge_develop/merge_master/merge_test  → merge_to / merge_all
  push_canary/push_dev/push_develop/push_master/push_test       → push_to / push_all
  merge_branch/push_branch → 分支名来自必填首参（例: merge_branch feature/x）

自动识别单仓/批量:
  - 当前目录是 git 仓库（有 .git）→ 单仓: merge_to / push_to
  - 非 git 目录 → 进批量扫描（merge_all / push_all）
"""
from __future__ import annotations

import pathlib
import sys

from lib.fire_base import BaseCli, run_cli, timed_cli


class GitWfCli(BaseCli):
    """merge*/push* 统一入口（action+target 由入口函数传入）"""

    def __init__(self, name: str, action: str, target: str) -> None:
        super().__init__()
        self._name = name
        self._action = action
        self._target = target

    def __call__(self, *, auto_commit: bool = False):
        """裸调用 `merge_*/push_*` 等同 `<cmd> auto`（cwd 是 git 仓库时 here，否则 all）"""
        return self.auto(auto_commit=auto_commit)

    @timed_cli
    def here(self, *, auto_commit: bool = False):
        """单仓模式：merge/push 当前分支 → <target>（target 由入口名决定）"""
        return self._dispatch(single=True, auto_commit=auto_commit)

    @timed_cli
    def all(self, *, auto_commit: bool = False):
        """批量模式：扫描所有 Git 仓库执行 merge/push → <target>"""
        return self._dispatch(single=False, auto_commit=auto_commit)

    @timed_cli
    def auto(self, *, auto_commit: bool = False):
        """自动判断：cwd 是 git 仓库 → here；否则 → all"""
        return self._dispatch(
            single=(pathlib.Path.cwd() / ".git").exists(),
            auto_commit=auto_commit,
        )

    def _dispatch(self, *, single: bool, auto_commit: bool) -> int:
        if not self._target:
            self._r.err(f"用法: {self._name} <分支名> [here|all|auto]  例: {self._name} feature/x")
            return 2
        # 让 run_workflow 显示用户输入的入口名
        sys.argv[0] = self._name
        argv = list(sys.argv) + ["--auto-commit"] if auto_commit else sys.argv

        if self._action == "merge":
            if single:
                from lib.git_workflow import merge_to
                return merge_to(self._target, argv)
            from lib.batch_git import merge_all
            return merge_all(self._target, argv)
        # push
        if single:
            from lib.git_workflow import push_to
            return push_to(self._target, argv)
        from lib.batch_git import push_all
        return push_all(self._target, argv)


def _pop_branch_arg(name: str) -> str:
    """*_branch 入口：分支名必须在 fire 解析前摘出，否则会被当成子命令。

    通用 flag（--skills/--dry-run/--no-say/--debug）先剥再找首个位置参：
    --skills/裸 --dry-run 就地退出 0（test_all_shells_common_flags 契约），
    剥完没有位置参但带 -h/--help 时放行走 fire help，否则报用法 exit 2。
    """
    from lib.notify import consume_debug, consume_dry_run, consume_no_say
    from lib.skills_help import consume_skills

    desc = GitWfCli.__doc__ or ""
    sys.argv[:] = consume_debug(consume_no_say(sys.argv))
    sys.argv[:] = consume_dry_run(sys.argv, description=desc)
    sys.argv[:] = consume_skills(sys.argv, description=desc)
    pos = next((i for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("-")), None)
    if pos is not None:
        return sys.argv.pop(pos)
    if not any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(f"用法: {name} <分支名> [here|all|auto]  例: {name} feature/x", file=sys.stderr)
        raise SystemExit(2)
    return ""


def _run(name: str, action: str, target: str | None) -> None:
    if target is None:
        target = _pop_branch_arg(name)
    run_cli(GitWfCli(name, action, target))


def merge_branch() -> None:
    _run("merge_branch", "merge", None)


def merge_canary() -> None:
    _run("merge_canary", "merge", "canary")


def merge_dev() -> None:
    _run("merge_dev", "merge", "dev")


def merge_develop() -> None:
    _run("merge_develop", "merge", "develop")


def merge_master() -> None:
    _run("merge_master", "merge", "master")


def merge_test() -> None:
    _run("merge_test", "merge", "test")


def push_branch() -> None:
    _run("push_branch", "push", None)


def push_canary() -> None:
    _run("push_canary", "push", "canary")


def push_dev() -> None:
    _run("push_dev", "push", "dev")


def push_develop() -> None:
    _run("push_develop", "push", "develop")


def push_master() -> None:
    _run("push_master", "push", "master")


def push_test() -> None:
    _run("push_test", "push", "test")

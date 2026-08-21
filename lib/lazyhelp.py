"""lazyhelp: 一页速查所有 bin/ 工具及功能。"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

from lib.ui import Reporter, reporter

# 名称 → (分类, 一句话功能)
# 描述取自各 bin 入口 docstring 末段（薄壳自描述）。
# 分类用于分组渲染；新增工具在此追加即可。
TOOLS: dict[str, tuple[str, str]] = {
    # git-wf: 批量分支 / 工作流
    "merge_canary": ("git-wf", "批量 merge 当前分支到 origin/canary"),
    "merge_develop": ("git-wf", "批量 merge 当前分支到 origin/develop"),
    "merge_master": ("git-wf", "批量 merge 当前分支到默认主分支（master/main）"),
    "merge_test": ("git-wf", "批量 merge 当前分支到 origin/test"),
    "push_canary": ("git-wf", "批量 push 当前分支到 origin/canary"),
    "push_develop": ("git-wf", "批量 push 当前分支到 origin/develop"),
    "push_master": ("git-wf", "批量 push 当前分支到默认主分支（master/main）"),
    "push_test": ("git-wf", "批量 push 当前分支到 origin/test"),
    "switch_branch": ("git-wf", "批量切换所有仓库到指定分支"),
    "sync_branch": ("git-wf", "批量同步各仓库指定分支到 origin/<branch>"),
    "sync_master": ("git-wf", "批量同步主分支（master/main）到 origin/<主分支>"),
    "delete_branch": ("git-wf", "删除本地分支（单仓 / 批量）"),
    "delete_branch_remote": ("git-wf", "删除远端分支（单仓 / 批量）"),
    "squash_pr": ("git-wf", "压 source 自分叉以来的改动为单 commit → 开 PR"),
    # git-ops: 单仓 / PR / Issue
    "commit": ("git-ops", "自动提交变更（单仓或批量扫描子目录）"),
    "mr": ("git-ops", "自动创建 PR/MR（调 claude 生成 title/body）"),
    "issue": ("git-ops", "自动创建 Issue（调 claude 生成 title/body）"),
    "fetch_all": ("git-ops", "一键拉取所有仓库远程更新"),
    "list_branch": ("git-ops", "列出所有仓库的本地分支"),
    "push_branch": ("git-ops", "批量推送当前分支到远端同名分支"),
    # process: 进程管理
    "kk": ("process", "按进程名终止进程（正则）"),
    "kkp": ("process", "按端口号终止占用进程"),
    # build/check: 编译 / 复制 / 检测
    "checkwork": ("build/check", "多语言编译检查（Go/Rust/Python/Java/Node）"),
    "check_ai": ("build/check", "AI API 端点连通性检测（空 POST）"),
    "cpd": ("build/check", "深度覆盖复制（新增/更新/删除可选）"),
    # loop/runtime: 循环执行 / 防休眠
    "loop": ("loop/runtime", "循环执行命令并追踪结果（成功即停或指定次数）"),
    "unsleep": ("loop/runtime", "防止 macOS 系统休眠（指定时长或跟随命令）"),
    # system: 系统 / 注入 / 通知
    "n": ("system", "macOS 语音播报（`say`）"),
    "disable-ipv6": ("system", "关闭本机所有网络服务的 IPv6"),
    "inject": ("system", "把 bin/ 注入 shell PATH（写入 ~/.zshrc 等）"),
}

CATEGORIES_ORDER = ["git-wf", "git-ops", "process", "build/check", "loop/runtime", "system"]


def _bin_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "bin"


def list_tools(category: str | None = None) -> list[tuple[str, str, str]]:
    """返回 [(name, category, description), ...]；category=None 全部，否则按分类过滤。"""
    rows: list[tuple[str, str, str]] = []
    for name in sorted(TOOLS):
        cat, desc = TOOLS[name]
        if category and cat != category:
            continue
        rows.append((name, cat, desc))
    return rows


def show_full(name: str) -> int:
    """调用 bin/<name> --help 输出完整说明；找不到返回非零。"""
    if name not in TOOLS:
        print(f"lazyhelp: 未知工具 {name!r}（试试 `lazyhelp --list`）", file=sys.stderr)
        return 2
    target = _bin_dir() / name
    if not target.exists():
        print(f"lazyhelp: 未找到 bin/{name}", file=sys.stderr)
        return 2
    # 透传 SCRIPTS_DEBUG / SCRIPTS_NO_SAY + 抑制嵌套 timed 输出噪音（最小化）
    env = os.environ.copy()
    env.setdefault("SCRIPTS_NO_SAY", "1")
    try:
        return subprocess.call([str(target), "--help"], env=env)
    except FileNotFoundError:
        print(f"lazyhelp: 无法执行 {target}", file=sys.stderr)
        return 1


def _render_table(rows: list[tuple[str, str, str]], r: Reporter) -> None:
    if not rows:
        r.warn("无匹配工具")
        return
    # 按分类聚合再分段
    by_cat: dict[str, list[tuple[str, str, str]]] = {}
    for name, cat, desc in rows:
        by_cat.setdefault(cat, []).append((name, cat, desc))

    for cat in CATEGORIES_ORDER:
        items = by_cat.get(cat)
        if not items:
            continue
        r.rule(f"{cat}（{len(items)}）", style="blue")
        max_name = max(len(n) for n, _, _ in items)
        for name, _, desc in items:
            r._print(
                f"[bold]{name:<{max_name}}[/bold]  [dim]·[/dim]  {desc}",
                f"  {name:<{max_name}}  ·  {desc}",
            )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="lazyhelp",
        description="一页速查所有 bin/ 工具及功能",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  lazyhelp                 # 全部分类概览\n"
            "  lazyhelp --list          # 仅名称列表\n"
            "  lazyhelp --category git-wf  # 按分类筛选\n"
            "  lazyhelp --full cpd      # 调 cpd --help 看完整说明\n"
            "  lazyhelp --search copy   # 按关键字搜索工具"
        ),
    )
    parser.add_argument("--list", action="store_true", help="仅输出工具名称（按字母排序）")
    parser.add_argument("--category", help="按分类筛选（git-wf/git-ops/process/build/check/loop/runtime/system）")
    parser.add_argument("--search", help="按关键字搜索（匹配名称或描述）")
    parser.add_argument("--full", metavar="NAME", help="调 bin/<NAME> --help 输出完整说明")
    args = parser.parse_args(argv[1:])

    r = reporter(stderr=True)

    if args.full:
        return show_full(args.full)

    rows = list_tools(category=args.category)
    if args.search:
        needle = args.search.lower()
        rows = [
            row for row in rows
            if needle in row[0].lower() or needle in row[2].lower()
        ]

    if args.list:
        for name, _, _ in rows:
            print(name)
        return 0

    r.rule(f"bin/ 工具速查（共 {len(rows)} 个）", style="blue")
    _render_table(rows, r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

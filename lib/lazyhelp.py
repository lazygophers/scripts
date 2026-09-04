"""lazyhelp: 一页速查所有 bin/ 工具及功能。"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

from rich.box import ROUNDED
from lib.ui import Reporter, reporter
from rich.table import Table

# 名称 → (分类, 一句话功能)
# 描述取自各 bin 入口 docstring 末段（薄壳自描述）。
# 分类用于分组渲染；新增工具在此追加即可。
TOOLS: dict[str, tuple[str, str]] = {
    # git-wf: 批量分支 / 工作流
    "merge_canary": ("git-wf", "批量 merge 当前分支到 origin/canary"),
    "merge_dev": ("git-wf", "批量 merge 当前分支到 origin/dev"),
    "merge_develop": ("git-wf", "批量 merge 当前分支到 origin/develop"),
    "merge_master": ("git-wf", "批量 merge 当前分支到默认主分支（master/main）"),
    "merge_test": ("git-wf", "批量 merge 当前分支到 origin/test"),
    "push_canary": ("git-wf", "批量 push 当前分支到 origin/canary"),
    "push_dev": ("git-wf", "批量 push 当前分支到 origin/dev"),
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
    "cicd": ("build/check", "轮询当前分支 CI/CD，完成后输出最终结果"),
    "cpd": ("build/check", "深度覆盖复制（新增/更新/删除可选）"),
    # loop/runtime: 循环执行 / 防休眠
    "loop": ("loop/runtime", "循环执行命令并追踪结果（成功即停或指定次数）"),
    "unsleep": ("loop/runtime", "防止 macOS 系统休眠（指定时长或跟随命令）"),
    # system: 系统 / 注入 / 通知
    "n": ("system", "macOS 语音播报（`say`）"),
    "disable-ipv6": ("system", "关闭本机所有网络服务的 IPv6"),
    "enable-ipv6": ("system", "开启本机所有网络服务的 IPv6"),
    "ipinfo": ("system", "查询内网 IP + 网络类型（含热点识别）"),
    "vpn-prio": ("system", "调整 macOS 网络服务优先级（压低 OpenVPN default 路由）"),
    "ovpn": ("system", "连 OpenVPN，自动填账号密码与二步验证码"),
    "archery": ("system", "Archery SQL 平台命令行客户端（按域名分别登录）"),
    "grafana": ("system", "Grafana HTTP API 命令行客户端（按域名分别登录）"),
    "inject": ("system", "把 bin/ 注入 shell PATH（写入 ~/.zshrc 等）"),
}

CATEGORIES_ORDER = ["git-wf", "git-ops", "process", "build/check", "loop/runtime", "system"]


def _bin_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "bin"


def _all_bins() -> list[str]:
    """bin/ 下所有可执行名（按字母排序）；含 _gitwf 与 lazyhelp。"""
    bin_dir = _bin_dir()
    return sorted(
        p.name for p in bin_dir.iterdir()
        if not p.name.startswith(".") and (p.is_file() or p.is_symlink())
    )


def show_full(name: str, *, extra_args: list[str] | None = None) -> int:
    """调 bin/<name> --help 输出完整说明（extra_args 透传给子命令）。"""
    target = _bin_dir() / name
    if not target.exists():
        print(f"lazyhelp: 未在 bin/ 中找到 {name!r}", file=sys.stderr)
        return 2
    args = [str(target), "--help", *(extra_args or [])]
    env = os.environ.copy()
    env.setdefault("SCRIPTS_NO_SAY", "1")  # 抑制嵌套 say 噪音
    try:
        return subprocess.call(args, env=env)
    except FileNotFoundError:
        print(f"lazyhelp: 无法执行 {target}", file=sys.stderr)
        return 1


def _render_table(rows: list[tuple[str, str, str]], r: Reporter) -> None:
    if not rows:
        r.warn("无匹配工具")
        return
    by_cat: dict[str, list[tuple[str, str, str]]] = {}
    for name, cat, desc in rows:
        by_cat.setdefault(cat, []).append((name, cat, desc))

    for cat in CATEGORIES_ORDER:
        items = by_cat.get(cat)
        if not items:
            continue
        table = Table(
            title=f"{cat}（{len(items)}）",
            show_header=False,
            box=ROUNDED,
            border_style="blue",
            title_style="bold",
        )
        table.add_column("工具", style="bold")
        table.add_column("说明")
        for name, _, desc in items:
            table.add_row(name, desc)
        r.console.print(table)


def main(argv: list[str]) -> int:
    r = reporter(stderr=True)

    # 任意位置参数 = 调对应 bin 的 --help（不区分是否注册在 TOOLS，
    # 只要 bin/ 下存在即可；未注册的 bin 也能查 help）。
    positional = argv[1:]
    if positional:
        name = positional[0]
        # 第一个参数若与 bin/ 下某条目同名 → 透传剩余参数给 bin/<name> --help
        bins = _all_bins()
        if name in bins:
            return show_full(name, extra_args=positional[1:])
        # 不在任何 bin/ 中 → 提示后打印概览
        r.warn(f"bin/ 下未找到 {name!r}（可用参数: {' / '.join(bins)}）")

    # 默认：打印全部分类速查
    rows: list[tuple[str, str, str]] = []
    for name in sorted(TOOLS):
        cat, desc = TOOLS[name]
        rows.append((name, cat, desc))

    r.rule(f"bin/ 工具速查（共 {len(rows)} 个）", style="blue")
    r.step("用法: lazyhelp <工具名>  # 输出该工具的完整 --help")
    _render_table(rows, r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

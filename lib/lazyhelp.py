"""lazyhelp: 一页速查所有 bin/ 工具及功能。"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

from rich.box import ROUNDED
from rich.table import Table

from lib.ui import Reporter, reporter

# 名称 → (分类, 一句话功能)
# 描述取自各 bin 入口 docstring 末段（薄壳自描述）。
# 分类按「拿它做什么」划分；新增工具在此追加即可。
TOOLS: dict[str, tuple[str, str]] = {
    # Git 工作流: 分支合并 / 推送 / 切换 / 删除 / 同步
    "merge_canary": ("Git 工作流", "合并当前分支到 canary（单仓 / 批量自动识别）"),
    "merge_dev": ("Git 工作流", "合并当前分支到 dev（单仓 / 批量自动识别）"),
    "merge_develop": ("Git 工作流", "合并当前分支到 develop（单仓 / 批量自动识别）"),
    "merge_master": ("Git 工作流", "合并当前分支到默认主分支（master/main 自动识别）"),
    "merge_test": ("Git 工作流", "合并当前分支到 test（单仓 / 批量自动识别）"),
    "merge_branch": ("Git 工作流", "合并当前分支到指定分支（分支名必填首参）"),
    "push_canary": ("Git 工作流", "推送当前分支到 canary 后切回原分支（单仓 / 批量）"),
    "push_dev": ("Git 工作流", "推送当前分支到 dev 后切回原分支（单仓 / 批量）"),
    "push_develop": ("Git 工作流", "推送当前分支到 develop 后切回原分支（单仓 / 批量）"),
    "push_master": ("Git 工作流", "推送当前分支到默认主分支后切回原分支（单仓 / 批量）"),
    "push_test": ("Git 工作流", "推送当前分支到 test 后切回原分支（单仓 / 批量）"),
    "push_branch": ("Git 工作流", "推送当前分支到指定分支（分支名必填首参）"),
    "switch_branch": ("Git 工作流", "批量切换所有仓库到指定分支（不存在则从默认主分支创建）"),
    "sync_branch": ("Git 工作流", "批量同步各仓库指定分支到 origin/<branch>"),
    "sync_master": ("Git 工作流", "批量同步各仓库默认主分支（master/main 自动识别）"),
    "delete_branch": ("Git 工作流", "删除本地分支（单仓 here / 批量 all）"),
    "delete_branch_remote": ("Git 工作流", "删除远端分支（单仓 here / 批量 all）"),
    # Git 协作: 提交 / PR / Issue / 巡检
    "commit": ("Git 协作", "自动提交变更（调 claude 生成 message；单仓或批量扫描子目录）"),
    "mr": ("Git 协作", "自动创建 PR/MR（调 claude 生成 title/body，默认 draft）"),
    "issue": ("Git 协作", "自动创建 Issue（调 claude 生成 title/body）"),
    "squash_pr": ("Git 协作", "压 source 自分叉以来的改动为单 commit → 开 PR"),
    "fetch_all": ("Git 协作", "一键拉取所有仓库远程更新（fetch all）"),
    "list_branch": ("Git 协作", "列出所有仓库的本地分支（跨仓同名分支标 ⟱）"),
    # 构建与检查: 编译闸门 / 端点探测 / CI 轮询
    "checkwork": ("构建与检查", "多语言编译检查（Go/Rust/Python/Java/Node），push 前闸门"),
    "check_ai": ("构建与检查", "AI API 端点连通性检测（空 POST）"),
    "cicd": ("构建与检查", "轮询当前分支 CI/CD，完成后输出最终结果"),
    # 数据与网络: 数据库 / 监控 / VPN / 本机网络
    "archery": ("数据与网络", "Archery SQL 平台命令行客户端（查询 / 上线工单，按域名分别登录）"),
    "grafana": ("数据与网络", "Grafana HTTP API 命令行客户端（按域名分别登录）"),
    "ovpn": ("数据与网络", "OpenVPN 客户端（自动填账号密码与二步验证码，支持分流）"),
    "vpn-prio": ("数据与网络", "调整 macOS 网络服务优先级（压低 OpenVPN default 路由）"),
    "ipinfo": ("数据与网络", "查询内网 IP + 网络类型（含热点识别）"),
    "disable-ipv6": ("数据与网络", "关闭本机所有网络服务的 IPv6（需 sudo）"),
    "enable-ipv6": ("数据与网络", "开启本机所有网络服务的 IPv6（需 sudo）"),
    # 网页检索: 搜索 / 抓取
    "websearch": ("网页检索", "多引擎网页检索（全引擎免 key 并行，按 URL 合并去重）"),
    "webgrab": ("网页检索", "抓网页转 Markdown（反爬直抓 + Playwright 渲染 + 34 站点适配 + 登录态持久化）"),
    # 进程与运行: 进程终止 / 循环 / 防休眠
    "kk": ("进程与运行", "按进程名终止进程（正则）"),
    "kkp": ("进程与运行", "按端口号终止占用进程"),
    "loop": ("进程与运行", "循环执行命令并追踪结果（成功即停或指定次数）"),
    "unsleep": ("进程与运行", "防止 macOS 系统休眠（指定时长或跟随命令）"),
    # 文件与系统: 复制 / 通知 / 注入 / 知识图谱
    "cpd": ("文件与系统", "深度覆盖复制（新增/更新/删除可选，md5 校验）"),
    "n": ("文件与系统", "macOS 语音播报（`say`）"),
    "inject": ("文件与系统", "把 bin/ 注入 shell PATH（写入 ~/.zshrc 等；macOS 可选启用 Touch ID sudo）"),
    "graphwatch": ("文件与系统", "graphify 全局 watch 守护服务：注册目录自动重建知识图谱"),
}

CATEGORIES_ORDER = ["Git 工作流", "Git 协作", "构建与检查", "数据与网络", "网页检索", "进程与运行", "文件与系统"]


def _bin_dir() -> pathlib.Path | None:
    """仓库内的 bin/ 目录；装成 Python 包后不存在（命令在 PATH 上），返回 None。"""
    d = pathlib.Path(__file__).resolve().parent.parent / "bin"
    return d if d.is_dir() else None


def _all_bins() -> list[str]:
    """所有命令名（按字母排序）。仓库内直接扫 bin/，装成包后退回 TOOLS 注册表。"""
    bin_dir = _bin_dir()
    if bin_dir is None:
        return sorted(TOOLS)
    return sorted(
        p.name for p in bin_dir.iterdir()
        if not p.name.startswith(".") and p.is_file()
    )


def _resolve(name: str) -> str | None:
    """命令的可执行路径：优先仓库内 bin/<name>，否则查 PATH。"""
    bin_dir = _bin_dir()
    if bin_dir is not None and (bin_dir / name).exists():
        return str(bin_dir / name)
    return shutil.which(name)


def show_full(name: str, *, extra_args: list[str] | None = None) -> int:
    """调 <name> --help 输出完整说明（extra_args 透传给子命令）。"""
    target = _resolve(name)
    if target is None:
        print(f"lazyhelp: 未在 bin/ 中找到 {name!r}", file=sys.stderr)
        return 2
    args = [target, "--help", *(extra_args or [])]
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

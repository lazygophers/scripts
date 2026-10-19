"""claude_session — 列出本机 Claude Code 会话

读 ~/.claude/sessions/*.json，输出表格：
名称 / 会话ID / 项目 / 状态 / 开始时间 / 最后活跃。
排序：当前目录的会话置顶，其余按项目分组，项目内按开始时间新→旧。
项目 = cwd 的 basename；同 basename 但 cwd 不同时带上一级目录消歧。

用法:
  claude_session                  全部会话
  claude_session -s 1907          sessionId 模糊过滤
  claude_session -p               只看当前目录所属项目
  claude_session -p sexy          只看项目名为 sexy 的会话
  claude_session --format json    输出 JSON（另可选 tsv / csv）
"""
from __future__ import annotations

import argparse
import sys

from lib.claude_session import _PROJECTS_ROOT
from lib.ui import print_tsv, timed
from pathlib import Path, PurePath

# 渲染列 → lib/claude_session.py 里的字段名
COLUMNS = [
    ("名称", "name"),
    ("会话ID", "sessionId"),
    ("项目", "project"),
    ("状态", "status"),
    ("开始时间", "start"),
    ("最后活跃", "active"),
]


def _print_table(sessions: list[dict]) -> None:
    from rich.console import Console
    from rich.table import Table

    table = Table(title="Claude Code 会话")
    for label, _ in COLUMNS:
        table.add_column(label)
    for s in sessions:
        table.add_row(*[str(s[key]) for _, key in COLUMNS])
    Console().print(table)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="claude_session", description="列出本机 Claude Code 会话")
    p.add_argument("-s", "--session", metavar="关键字",
                   help="按 sessionId 模糊过滤（不区分大小写子串）")
    p.add_argument("-p", "--project", nargs="?", const="", default=None,
                   metavar="项目名",
                   help="精准匹配项目名；省略值 = 当前目录所属项目")
    p.add_argument("-f", "--format", choices=["json", "tsv", "csv"],
                   default=None, help="输出格式；默认 AI 环境 TSV，人类环境表格")
    p.add_argument("--all", action="store_true",
                   help="扫描 ~/.claude/projects/ 下的会话日志（jsonl 路径）")
    p.add_argument("--grep", metavar="关键词", default=None,
                   help="在 summary / 首条消息 / 末尾消息里按关键词过滤（不区分大小写）")
    p.add_argument("--tail", type=int, default=3, metavar="N",
                   help="每条会话末尾取 N 条用户消息，默认 3")
    return p.parse_args(argv)


def _check_grep_tail_gated(args: argparse.Namespace) -> None:
    """--grep / --tail 只在 --all 下生效，否则报错退出。"""
    if (args.grep is not None or args.tail != 3) and not args.all:
        p = argparse.ArgumentParser(prog="claude_session")
        p.error("--grep / --tail 只能在 --all 下使用")


def _emit(sessions: list[dict], fmt: str) -> None:
    import csv
    import io

    from lib.ai_env import json_dumps

    headers = [label for label, _ in COLUMNS]
    rows = [[str(s[key]) for _, key in COLUMNS] for s in sessions]
    if fmt == "json":
        print(json_dumps([dict(zip((k for _, k in COLUMNS), r))
                          for r in rows]))
    elif fmt == "csv":
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows([headers, *rows])
        print(buf.getvalue(), end="")
    elif fmt == "tsv":
        print_tsv(headers, rows)
    else:
        _print_table(sessions)


# --all 路径的字段集
_LOG_COLUMNS = ["sessionId", "project", "start", "last", "cwd",
                "git_branch", "kind", "description"]


def _description(row: dict) -> str:
    """拼 description 单元：summary= / first= / last= 三段。"""
    last_msgs = row.get("last_messages") or []
    last_str = " | ".join(last_msgs) if last_msgs else ""
    return "\n".join([
        f"summary={row.get('summary', '')}",
        f"first={row.get('first_message', '')}",
        f"last={last_str}",
    ])


def _emit_logs(rows: list[dict], fmt: str) -> None:
    import csv
    import io

    from lib.ai_env import json_dumps

    if fmt == "json":
        print(json_dumps(rows))
        return

    headers = list(_LOG_COLUMNS)
    prepared = []
    for r in rows:
        desc = _description(r)
        prepared.append([str(r.get(k, "")) for k in _LOG_COLUMNS[:-1]] + [desc])

    if fmt == "tsv":
        print_tsv(headers, prepared)
    elif fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(prepared)
        print(buf.getvalue(), end="")
    elif fmt == "table":
        from rich.console import Console
        from rich.table import Table
        table = Table()
        for h in headers:
            table.add_column(h)
        for vals in prepared:
            table.add_row(*vals)
        Console().print(table)
    else:
        _emit_logs(rows, "json")


def main(argv: list[str] | None = None) -> int:
    from lib.claude_session import filter_sessions, filter_session_logs, list_sessions, list_session_logs
    from lib.notify import consume_debug, consume_no_say, consume_dry_run
    from lib.skills_help import consume_skills

    argv = consume_dry_run(
        consume_skills(
            consume_debug(consume_no_say(list(sys.argv if argv is None else argv))),
            __doc__))
    args = _parse_args(argv[1:])
    _check_grep_tail_gated(args)
    fmt = args.format
    if fmt is None:
        from lib.ai_env import is_ai_shell_env
        fmt = "tsv" if is_ai_shell_env() else "table"

    if args.all:
        rows = timed(list_session_logs, label="claude_session")(
            tail=args.tail)
        rows = filter_session_logs(rows, grep=args.grep)
        if args.project is not None:
            import hashlib
            cwd = str(Path.cwd().resolve())
            cwd_hash = hashlib.sha256(cwd.encode()).hexdigest()[:12]
            target = _PROJECTS_ROOT / cwd_hash
            if not target.exists():
                sessions = []
            else:
                sessions = [r for r in rows
                            if PurePath(r.get("cwd") or "") == Path(cwd)]
        else:
            sessions = rows
        _emit(sessions, fmt)
        return 0

    sessions = timed(list_sessions, label="claude_session")()
    sessions = filter_sessions(sessions, session_kw=args.session,
                               project=args.project)
    _emit(sessions, fmt)
    return 0

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

from lib.ui import print_tsv, timed

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
    return p.parse_args(argv)


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


def main(argv: list[str] | None = None) -> int:
    from lib.claude_session import filter_sessions, list_sessions
    from lib.notify import consume_debug, consume_no_say, consume_dry_run
    from lib.skills_help import consume_skills

    argv = consume_dry_run(
        consume_skills(
            consume_debug(consume_no_say(list(sys.argv if argv is None else argv))),
            __doc__))
    args = _parse_args(argv[1:])
    sessions = timed(list_sessions, label="claude_session")()
    sessions = filter_sessions(sessions, session_kw=args.session,
                               project=args.project)
    fmt = args.format
    if fmt is None:
        from lib.ai_env import is_ai_shell_env

        fmt = "tsv" if is_ai_shell_env() else "table"
    _emit(sessions, fmt)
    return 0

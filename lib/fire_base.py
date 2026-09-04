"""fire 公共基底：BaseCli + 入口工具函数。

子命令方法里通过 `self._r` 访问 Rich Reporter；属性名加下划线前缀
避免 fire 反射成 GROUPS 里那个名为 `r` 的占位项（fire 把所有公开实例
属性当 group 列出来）。
"""

from __future__ import annotations

import os
import sys
import time
from functools import wraps
from typing import Any, Callable

import fire

from lib.ui import Reporter, reporter


class BaseCli:
    """fire CLI 基类。子类直接写方法即可被 fire.Fire() 反射成子命令。

    注意：fire 把 BaseCli 实例属性视为 values/flags（不能作为子命令参数前缀）。
    因此全局 `--dry-run` / `--no-say` / `--debug` 不放在 self 上，改走
    consume_* 拦截器（见 run_cli）。
    """

    def __init__(self) -> None:
        self._r: Reporter = reporter(stderr=True)


def timed_cli(method: Callable[..., Any]) -> Callable[..., Any]:
    """装饰 bin/* CLI 方法：跑完用 Rich 打 dim 灰色耗时（含起止时间）。

    自适应单位：<1s 显示 ms；<60s 显示 s；≥1m 显示 m+s。
    走 Rich Console(stderr=True) → dim 样式统一。
    透传方法返回值（fire 用作 exit code）。
    """

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        from datetime import datetime

        from rich.console import Console
        from rich.text import Text

        t0 = time.monotonic()
        start_wall = time.time()
        try:
            return method(self, *args, **kwargs)
        finally:
            elapsed = time.monotonic() - t0
            ms = int(elapsed * 1000)
            if ms < 1000:
                elapsed_s = f"{ms}ms"
            elif elapsed < 60:
                elapsed_s = f"{elapsed:.1f}s"
            else:
                m, s = divmod(int(elapsed), 60)
                elapsed_s = f"{m}m{s}s"
            start_s = datetime.fromtimestamp(start_wall).strftime("%H:%M:%S")
            end_s = datetime.fromtimestamp(time.time()).strftime("%H:%M:%S")
            con = Console(stderr=True)
            t = Text()
            t.append("⏱ ", style="dim")
            t.append(elapsed_s, style="dim bold")
            t.append(f" · {start_s}–{end_s}", style="dim")
            con.print(t)

    return wrapper


def run_cli(cli: BaseCli) -> None:
    """fire 入口：把 sys.argv 喂给 fire.Fire(cli)，并把方法返回值转成 exit code。"""
    from lib.notify import consume_debug, consume_dry_run, consume_no_say
    from lib.skills_help import consume_skills

    argv = list(sys.argv)
    argv = consume_skills(argv, description=cli.__class__.__doc__ or "")
    argv = consume_dry_run(argv, description=cli.__class__.__doc__ or "")
    argv = consume_debug(argv)
    argv = consume_no_say(argv)
    sys.argv = argv
    os.environ.setdefault("PAGER", "-")

    import fire.console.console_io as _cio
    _cio.More = lambda contents, out, prompt=None, check_pager=True: out.write(contents)

    import fire.core as _fc
    _fc.Display = lambda lines, out: _render_fire_help(lines, out)

    import builtins as _bi
    _real_print = _bi.print
    _bi.print = lambda *a, **kw: (_render_fire_info(a, kw) if a and isinstance(a[0], str) and a[0].startswith("INFO: ") else _real_print(*a, **kw))

    _fc._PrintResult = lambda component_trace, verbose=False, serialize=None: _handle_fire_result(component_trace)

    result = fire.Fire(cli)
    _bi.print = _real_print
    if isinstance(result, int):
        sys.exit(result)
    sys.exit(0)


def _handle_fire_result(component_trace) -> None:
    """吞掉 fire._PrintResult：返回值由 run_cli 末尾的 sys.exit 决定。"""


def _render_fire_info(args, kwargs) -> None:
    """替代 fire.core 内部的 print('INFO: Showing help ...')：改成一行 dim 提示。"""
    from rich.console import Console
    c = Console(stderr=True, force_terminal=True, highlight=False)
    c.print(f"[dim]{args[0].rstrip()}[/dim]")


def _render_fire_help(lines, out) -> None:
    """接管 fire.core.Display：把 Fire help 压成彩色短版。"""
    text = "\n".join(lines).strip("\n")
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = Console(file=out, force_terminal=True, highlight=False)
    name, desc = _help_name(text)
    synopsis = _help_section(text, "SYNOPSIS").strip().splitlines()
    description = _help_section(text, "DESCRIPTION").strip().splitlines()
    groups = _help_choices(text, "GROUPS")
    commands = _help_choices(text, "COMMANDS")

    head = Text()
    head.append(name or "help", style="bold cyan")
    if desc:
        head.append(f" — {desc}", style="dim")
    console.print(head)

    if synopsis:
        console.print(f"[bold green]用法[/bold green] {synopsis[0].strip()}")

    common = [line.strip() for line in description if line.strip() and line.strip() != desc]
    if common:
        for line in common[:8]:
            console.print(f"[dim]{line}[/dim]")

    _render_help_table(console, "命令组", groups)
    _render_help_table(console, "命令", commands)


def _help_section(text: str, name: str) -> str:
    marker = f"\n{name}\n"
    start = text.find(marker)
    if start == -1:
        start = 0 if text.startswith(name + "\n") else -1
    if start == -1:
        return ""
    start += len(marker) if start else len(name) + 1
    next_pos = len(text)
    for title in ("NAME", "SYNOPSIS", "DESCRIPTION", "POSITIONAL ARGUMENTS", "ARGUMENTS", "FLAGS", "GROUPS", "COMMANDS", "VALUES", "INDEXES", "NOTES"):
        if title == name:
            continue
        pos = text.find(f"\n{title}\n", start)
        if pos != -1:
            next_pos = min(next_pos, pos)
    return text[start:next_pos]


def _help_name(text: str) -> tuple[str, str]:
    first = _help_section(text, "NAME").strip().splitlines()
    if not first:
        return "", ""
    raw = first[0].strip()
    if " - " in raw:
        name, desc = raw.split(" - ", 1)
        return name.strip(), desc.strip()
    return raw, ""


def _help_choices(text: str, section: str) -> list[tuple[str, str]]:
    body = _help_section(text, section).splitlines()
    choices: list[tuple[str, str]] = []
    i = 0
    while i < len(body):
        line = body[i]
        stripped = line.strip()
        if not stripped or stripped.endswith("is one of the following:"):
            i += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= 6:
            desc = ""
            if i + 1 < len(body):
                next_line = body[i + 1]
                next_stripped = next_line.strip()
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_stripped and next_indent > indent:
                    desc = next_stripped
                    i += 1
            choices.append((stripped, desc))
        i += 1
    return choices


def _render_help_table(console, title: str, rows: list[tuple[str, str]]) -> None:
    if not rows:
        return
    console.print(f"[bold blue]{title}[/bold blue]")
    for name, desc in rows:
        console.print(f"  [bold blue]{name:<10}[/bold blue] [white]{_clip(desc, 52)}[/white]")


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width - 1] + "…"

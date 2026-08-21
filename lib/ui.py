"""Rich 统一输出（强制 Rich，无 Rich 直接报错退出）。

所有 bin/* 工具依赖 Rich 美化输出。未安装 Rich 时脚本直接抛 RuntimeError，
提示用户 `pip install rich`。不提供任何静默降级路径。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.box import ROUNDED
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.rule import Rule
    from rich.style import Style
    from rich.table import Table
    from rich.text import Text

    HAS_RICH = True
except Exception as _rich_err:
    # 启动期就报错：bin/* 调用 Reporter 前必先 import lib.ui，
    # 这里 raise 让调用方看到清晰提示。
    raise RuntimeError(
        "scripts 工具依赖 Rich 美化输出。请先安装:\n"
        "  pip install rich\n"
        f"原始错误: {_rich_err}"
    ) from _rich_err


# === 样式常量 ===
STYLE_SUCCESS = Style(color="green", bold=True)
STYLE_ERROR = Style(color="red", bold=True)
STYLE_WARNING = Style(color="yellow", bold=True)
STYLE_INFO = Style(color="cyan")
STYLE_STEP = Style(color="blue", bold=True)
STYLE_DIM = Style(dim=True)

# 图标
ICON_SUCCESS = "✓"
ICON_ERROR = "✗"
ICON_WARNING = "⚠"
ICON_INFO = "ℹ"
ICON_STEP = "→"
ICON_ARROW = "▸"
ICON_SKIP = "•"

# 状态 → (图标, 色)；批量汇总 / 执行段共用
STATUS_STYLE = {
    "ok": (ICON_SUCCESS, "green"),
    "skip": (ICON_SKIP, "yellow"),
    "fail": (ICON_ERROR, "red"),
}
STATUS_LABEL = {"ok": "成功", "skip": "跳过", "fail": "失败"}


def console(stderr: bool = False) -> Console:
    return Console(stderr=stderr)


def progress(console_obj: Console | None) -> Progress:
    if console_obj is None:
        raise ValueError("progress() 需要 console_obj")
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}[/bold]"),
        BarColumn(bar_width=None),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console_obj,
        transient=True,  # 完成后自动清掉该行，不残留
    )


def print_ansi(console_obj: Console | None, text: str) -> None:
    """把含 ANSI / Rich 标记的文本原样转写到 console。"""
    if console_obj is None:
        raise ValueError("print_ansi() 需要 console_obj")
    console_obj.print(Text.from_ansi(text))


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


class Reporter:
    """统一输出（强制 Rich 美化，无任何降级）。"""

    def __init__(self, *, stderr: bool = True, console: Console | None = None,
                 file: object | None = None) -> None:
        if file is not None:
            self.console = Console(file=file, stderr=False)
        elif console is not None:
            self.console = console
        else:
            self.console = Console(stderr=stderr)
        self.stderr = stderr
        self._file = file

    @classmethod
    def from_buffer(cls, buf: object) -> Reporter:
        """构造写入 StringIO buffer 的 Reporter（线程内重定向用）。"""
        return cls(file=buf)

    def _print(self, rich_text, plain_text: str) -> None:
        self.console.print(rich_text)

    def _icon_msg(self, icon: str, msg: str, color: str) -> None:
        text = Text()
        text.append(icon, style=f"bold {color}")
        text.append(" ")
        text.append(msg, style=color)
        self.console.print(text)

    def status(self, status: str, msg: str) -> None:
        """按状态选图标 + 色（ok=✓绿 / skip=•黄 / fail=✗红）。"""
        icon, color = STATUS_STYLE.get(status, (ICON_INFO, "cyan"))
        self._icon_msg(icon, msg, color)

    def status_table(
        self,
        title: str,
        items: Sequence[tuple[str, str, str]],
        *,
        columns: Sequence[str] = ("仓库", "状态", "详情"),
    ) -> None:
        """状态汇总表：items 为 (name, status, detail) 三元组列表，状态列按状态着色。"""
        table = Table(title=title, show_header=True, box=ROUNDED, border_style="blue",
                      title_style="bold", header_style="bold cyan")
        table.add_column(columns[0], style="bold")
        table.add_column(columns[1])
        table.add_column(columns[2])
        for name, status, detail in items:
            color = STATUS_STYLE.get(status, ("", "white"))[1]
            label = STATUS_LABEL.get(status, status)
            table.add_row(name, f"[{color}]{label}[/{color}]", detail)
        self.console.print(table)

    def status_footer(self, parts: Sequence[tuple[str, str]]) -> None:
        """单行统计 footer：parts 为 (text, color) 列表，用 · 连接，各段按其色。"""
        if not parts:
            return
        text = Text()
        for i, (s, color) in enumerate(parts):
            if i > 0:
                text.append(" · ", style="dim")
            text.append(s, style=color)
        self.console.print(text)

    def rule(self, title: str, *, style: str = "blue") -> None:
        self.console.print(Rule(f"[bold]{title}[/bold]", style=style))

    def panel(self, title: str, content: str, *, style: str = "blue") -> None:
        self.console.print(Panel(content, title=title, border_style=style))

    def info(self, msg: str) -> None:
        self._icon_msg(ICON_INFO, msg, "cyan")

    def step(self, msg: str) -> None:
        self._icon_msg(ICON_STEP, msg, "blue")

    def ok(self, msg: str) -> None:
        self._icon_msg(ICON_SUCCESS, msg, "green")

    def warn(self, msg: str) -> None:
        self._icon_msg(ICON_WARNING, msg, "yellow")

    def err(self, msg: str) -> None:
        self._icon_msg(ICON_ERROR, msg, "red")

    def kv(self, title: str, rows: dict[str, str], *, style: str = "blue") -> None:
        table = Table(title=title, show_header=False, box=ROUNDED, border_style=style)
        table.add_column("Key", style="bold")
        table.add_column("Value")
        for k, v in rows.items():
            table.add_row(str(k), str(v))
        self.console.print(table)

    def cmd_result(
        self,
        cmd: Sequence[str],
        *,
        cwd: str | None = None,
        returncode: int | None = None,
        output: str = "",
        show_output: bool = False,
        title: str = "",
    ) -> None:
        from lib.exec import shell_join
        cmd_s = shell_join(cmd)
        where = f" (cwd={cwd})" if cwd else ""
        head = f"{title}: {cmd_s}{where}" if title else f"{cmd_s}{where}"

        if returncode is None or returncode == 0:
            self.step(head)
        else:
            self.err(f"{head} (exit={returncode})")

        if show_output and output.strip():
            self.output(output)

    def output(self, text: str, *, max_lines: int = 30, prefix: str = "  ") -> None:
        t = (text or "").rstrip()
        if not t:
            return
        lines = t.splitlines()
        truncated = False
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            truncated = True
        for line in lines:
            self.console.print(f"[dim]{prefix}{line}[/dim]")
        if truncated:
            self.console.print(
                f"[dim]{prefix}... (+{len(t.splitlines()) - max_lines} 行)[/dim]"
            )

    def summary(self, title: str, items: list[tuple[str, str, str | None]]) -> None:
        table = Table(title=title, show_header=False, box=ROUNDED)
        table.add_column("Label", style="bold")
        table.add_column("Value")
        for label, value, style in items:
            if style:
                table.add_row(label, f"[{style}]{value}[/{style}]")
            else:
                table.add_row(label, value)
        self.console.print(table)


def reporter(*, stderr: bool = True) -> Reporter:
    return Reporter(stderr=stderr)


def ask_confirm(question: str, *, default: bool = False) -> bool | None:
    """是/否确认。强制 Rich：走 Confirm（带色 + 默认值提示）；非交互（EOF）返回 None。"""
    from rich.prompt import Confirm
    try:
        return Confirm.ask(question, default=default, console=Console())
    except (EOFError, KeyboardInterrupt):
        return None


def ask_text(prompt: str, *, default: str = "") -> str | None:
    """文本输入。强制 Rich：走 Prompt（带色 + 默认值）；非交互（EOF）返回 None。"""
    from rich.prompt import Prompt
    try:
        return Prompt.ask(prompt, default=default, console=Console())
    except (EOFError, KeyboardInterrupt):
        return None


def _format_elapsed(seconds: float) -> str:
    """耗时人话格式：<1s → '823ms'；<60s → '12.3s'；否则 → '1m23s'。"""
    if seconds < 1:
        ms = int(seconds * 1000)
        return f"{ms}ms" if ms > 0 else "<1ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m{s}s"
    h, m = divmod(m, 60)
    return f"{h}h{m}m{s}s"


def print_runtime(start: float, end: float, *, label: str | None = None) -> None:
    """灰度打印运行耗时（耗时为核心，起止时间括号附注）。

    强制 Rich：耗时数字微亮，起止时间 dim；走 rich.Console 到 stderr。
    格式: ⏱ <label> · <耗时> · <起>–<止>
    """
    from datetime import datetime
    from rich.text import Text
    fmt = "%H:%M:%S"
    start_s = datetime.fromtimestamp(start).strftime(fmt)
    end_s = datetime.fromtimestamp(end).strftime(fmt)
    elapsed = _format_elapsed(end - start)
    head = f"⏱ {label}" if label else "⏱"
    con = Console(stderr=True)
    t = Text()
    t.append(f"{head} · ", style="dim")
    t.append(elapsed, style="dim bold")
    t.append(f" · {start_s}–{end_s}", style="dim")
    con.print(t)


def timed(fn, *, label: str | None = None):
    """装饰/包装：包住 fn 全程计时，结束灰度打印运行时间。

    用法（bin 入口）：
        raise SystemExit(timed(main, label="commit")(sys.argv))
    返回值/异常原样透传；异常路径也打印耗时（finally）。
    """
    import time
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        start = time.monotonic()
        start_wall = time.time()
        try:
            return fn(*args, **kwargs)
        finally:
            end_wall = time.time()
            print_runtime(start_wall, end_wall, label=label)

    return wrapper

"""Rich 统一输出（自动降级纯文本）。"""

from __future__ import annotations

import sys
from collections.abc import Sequence

try:
    from rich.console import Console
    from rich.panel import Panel
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
except Exception:
    Console = None  # type: ignore[assignment,misc]
    Progress = None  # type: ignore[assignment,misc]
    Rule = None  # type: ignore[assignment,misc]
    Table = None  # type: ignore[assignment,misc]
    Panel = None  # type: ignore[assignment,misc]
    Text = None  # type: ignore[assignment,misc]
    Style = None  # type: ignore[assignment,misc]
    HAS_RICH = False


# === 样式常量 ===
STYLE_SUCCESS = Style(color="green", bold=True) if HAS_RICH else None
STYLE_ERROR = Style(color="red", bold=True) if HAS_RICH else None
STYLE_WARNING = Style(color="yellow", bold=True) if HAS_RICH else None
STYLE_INFO = Style(color="cyan") if HAS_RICH else None
STYLE_STEP = Style(color="blue", bold=True) if HAS_RICH else None
STYLE_DIM = Style(dim=True) if HAS_RICH else None

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


def console(stderr: bool = False) -> Console | None:
    if not HAS_RICH:
        return None
    return Console(stderr=stderr)


def progress(console_obj: Console | None) -> Progress | None:
    if not HAS_RICH or console_obj is None:
        return None
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}[/bold]"),
        BarColumn(bar_width=None),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console_obj,
        transient=False,
    )


def print_ansi(console_obj: Console | None, text: str) -> bool:
    """把含 ANSI / Rich 标记的文本原样转写到 console。无 rich 返回 False 由调用方降级。"""
    if console_obj is None or Text is None:
        return False
    console_obj.print(Text.from_ansi(text))
    return True


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


class Reporter:
    """统一输出：支持 Rich 美化输出，自动降级纯文本。"""

    def __init__(self, *, stderr: bool = True, console: Console | None = None,
                 file: object | None = None) -> None:
        if file is not None and HAS_RICH:
            self.console = Console(file=file, stderr=False)
        elif console is not None:
            self.console = console
        elif file is not None:
            # 无 Rich：靠 _file 直接写
            self.console = None
        else:
            self.console = Console(stderr=stderr) if HAS_RICH else None
        self.stderr = stderr
        self._file = file  # plain 模式直写对象（StringIO）

    @classmethod
    def from_buffer(cls, buf: object) -> Reporter:
        """构造写入 StringIO buffer 的 Reporter（线程内重定向用）。

        Rich 可用时 Console(file=buf)；否则用内置 _file 直写（绕过 stderr/stdout）。
        """
        return cls(file=buf)

    def _print(self, rich_text, plain_text: str) -> None:
        if self.console is not None:
            self.console.print(rich_text)
        elif self._file is not None:
            print(plain_text, file=self._file)
        elif self.stderr:
            _eprint(plain_text)
        else:
            print(plain_text)

    def _icon_msg(self, icon: str, msg: str, color: str) -> None:
        if self.console is not None and Text is not None:
            text = Text()
            text.append(icon, style=f"bold {color}")
            text.append(" ")
            # 消息文本随 icon 同色（PRD 颜色规范）
            text.append(msg, style=color)
            self.console.print(text)
        else:
            self._print("", f"{icon} {msg}")

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
        if self.console is not None and Table is not None:
            table = Table(title=title, show_header=True, box=None, border_style="blue",
                          title_style="bold", header_style="dim")
            table.add_column(columns[0], style="bold")
            table.add_column(columns[1])
            table.add_column(columns[2])
            for name, status, detail in items:
                color = STATUS_STYLE.get(status, ("", "white"))[1]
                label = STATUS_LABEL.get(status, status)
                table.add_row(name, f"[{color}]{label}[/{color}]", detail)
            self.console.print(table)
            return
        # 降级纯文本：name + 状态标签 + 详情，单行
        self.rule(title)
        for name, status, detail in items:
            label = STATUS_LABEL.get(status, status)
            line = f"  {name}  {label}"
            if detail:
                line += f"  {detail}"
            self._print("", line)

    def status_footer(self, parts: Sequence[tuple[str, str]]) -> None:
        """单行统计 footer：parts 为 (text, color) 列表，用 · 连接，各段按其色。"""
        if not parts:
            return
        if self.console is not None and Text is not None:
            text = Text()
            for i, (s, color) in enumerate(parts):
                if i > 0:
                    text.append(" · ", style="dim")
                text.append(s, style=color)
            self.console.print(text)
        else:
            self._print("", " · ".join(s for s, _ in parts))

    def rule(self, title: str, *, style: str = "blue") -> None:
        if self.console is not None and Rule is not None:
            self.console.print(Rule(f"[bold]{title}[/bold]", style=style))
        else:
            self._print("", f"\n{'═' * 10} {title} {'═' * 10}")

    def panel(self, title: str, content: str, *, style: str = "blue") -> None:
        if self.console is not None and Panel is not None:
            self.console.print(Panel(content, title=title, border_style=style))
        else:
            self.rule(title)
            for line in content.splitlines():
                self._print("", f"  {line}")

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
        if self.console is not None and Table is not None:
            table = Table(title=title, show_header=False, box=None, border_style=style)
            table.add_column("Key", style="bold")
            table.add_column("Value")
            for k, v in rows.items():
                table.add_row(str(k), str(v))
            self.console.print(table)
            return
        self.rule(title)
        max_key_len = max(len(str(k)) for k in rows.keys()) if rows else 0
        for k, v in rows.items():
            self._print("", f"  {k:<{max_key_len}}  {v}")

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
        if self.console is not None:
            for line in lines:
                self._print(f"[dim]{prefix}{line}[/dim]", f"{prefix}{line}")
            if truncated:
                self._print(f"[dim]{prefix}... (+{len(t.splitlines()) - max_lines} 行)[/dim]", f"{prefix}... (+更多)")
        else:
            for line in lines:
                self._print("", f"{prefix}{line}")
            if truncated:
                self._print("", f"{prefix}... (+更多)")

    def summary(self, title: str, items: list[tuple[str, str, str | None]]) -> None:
        if self.console is not None and Table is not None:
            table = Table(title=title, show_header=False, box=None)
            table.add_column("Label", style="bold")
            table.add_column("Value")
            for label, value, style in items:
                if style:
                    table.add_row(label, f"[{style}]{value}[/{style}]")
                else:
                    table.add_row(label, value)
            self.console.print(table)
        else:
            self.rule(title)
            for label, value, _ in items:
                self._print("", f"  {label}: {value}")


def reporter(*, stderr: bool = True) -> Reporter:
    return Reporter(stderr=stderr)


def _format_elapsed(seconds: float) -> str:
    """耗时人话格式：<1s → '0.8s'；<60s → '12.3s'；否则 → '1m23s'。"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m{s}s"
    h, m = divmod(m, 60)
    return f"{h}h{m}m{s}s"


def print_runtime(start: float, end: float, *, label: str | None = None) -> None:
    """灰度打印运行时间（开始/结束/耗时）。供各 bin 入口在最外层调用。

    Rich 可用时走 dim 样式；否则纯文本（仍到 stderr，与 Reporter 默认一致）。
    """
    from datetime import datetime
    fmt = "%H:%M:%S"
    start_s = datetime.fromtimestamp(start).strftime(fmt)
    end_s = datetime.fromtimestamp(end).strftime(fmt)
    elapsed = _format_elapsed(end - start)
    prefix = f"{label} " if label else ""
    line = f"{prefix}开始 {start_s} · 结束 {end_s} · 耗时 {elapsed}"
    if HAS_RICH:
        con = Console(stderr=True)
        con.print(line, style="dim")
    else:
        print(line, file=sys.stderr)


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

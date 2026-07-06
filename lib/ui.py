"""Rich 统一输出（自动降级纯文本）。"""
import sys
from typing import Optional, Sequence, Tuple

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


def console(stderr: bool = False) -> Optional["Console"]:
    if not HAS_RICH:
        return None
    return Console(stderr=stderr)


def progress(console_obj: Optional["Console"]) -> Optional["Progress"]:
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


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


class Reporter:
    """统一输出：支持 Rich 美化输出，自动降级纯文本。"""

    def __init__(self, *, stderr: bool = True, console: Optional["Console"] = None,
                 file: Optional["object"] = None) -> None:
        if file is not None and HAS_RICH:
            self.console = Console(file=file, stderr=False)
        elif console is not None:
            self.console = console
        elif file is not None:
            # 无 Rich：靠 _file 直接写
            self.console = None
        else:
            self.console = console(stderr=stderr) if HAS_RICH else None
        self.stderr = stderr
        self._file = file  # plain 模式直写对象（StringIO）

    @classmethod
    def from_buffer(cls, buf: "object") -> "Reporter":
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
            text.append(msg)
            self.console.print(text)
        else:
            self._print("", f"{icon} {msg}")

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
        cwd: Optional[str] = None,
        returncode: Optional[int] = None,
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

    def summary(self, title: str, items: list[Tuple[str, str, Optional[str]]]) -> None:
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

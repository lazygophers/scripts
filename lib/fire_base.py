"""fire 公共基底：BaseCli + 入口工具函数。"""

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

    self.r: Reporter — 统一 Rich 输出（含降级）

    注意：fire 把 BaseCli 实例属性视为 values/flags（不能作为子命令参数前缀）。
    因此全局 `--dry-run` / `--no-say` / `--debug` 不放在 self 上，改走
    consume_* 拦截器（见 run_cli）。
    """

    def __init__(self) -> None:
        self.r: Reporter = reporter(stderr=True)


def timed_cli(method: Callable[..., Any]) -> Callable[..., Any]:
    """装饰 bin/* CLI 方法：跑完打印耗时（与 lib.ui.timed 同语义，但只打印一次）。

    透传方法返回值（fire 用作 exit code）。
    """

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        t0 = time.monotonic()
        try:
            return method(self, *args, **kwargs)
        finally:
            ms = (time.monotonic() - t0) * 1000
            print(f"⏱ {method.__qualname__} · {ms:.1f}ms", file=sys.stderr)

    return wrapper


def run_cli(cli: BaseCli) -> None:
    """fire 入口：把 sys.argv 喂给 fire.Fire(cli)，并把方法返回值转成 exit code。

    fire 把 BaseCli 实例属性视作 values/flags；为符合现有
    `--no-say` / `--debug` / `--dry-run` 协议，预先从 argv 剥除这三个
    flag 并设置到环境变量，再调 fire.Fire。

    方法返回 int 时，fire 不会自动转 exit code（fire 本身把返回值当字符串打印）；
    这里显式 sys.exit 把 int 转成退出码。None 当作 0。

    PAGER=- + monkey-patch fire.console.console_io.More 直出，避免 pager。
    fire.core.Display 被替换为 _render_help：识别 fire help 文本 → rich
    Panel 高亮（保留 fire 原段落结构，仅替换标题色 + 整体加框）。
    无 Rich 时降级纯文本。
    """
    from lib.notify import consume_debug, consume_dry_run, consume_no_say

    argv = list(sys.argv)
    argv = consume_dry_run(argv, description=cli.__class__.__doc__ or "")
    argv = consume_debug(argv)
    argv = consume_no_say(argv)
    sys.argv = argv
    os.environ.setdefault("PAGER", "-")

    # 禁用 pager
    import fire.console.console_io as _cio
    _cio.More = lambda contents, out, prompt=None, check_pager=True: out.write(contents)

    # 拦截 fire.core.Display：help 文本走 rich 美化（只着色 + Panel 边框，不重排内容）
    import fire.core as _fc
    _fc.Display = lambda lines, out: _render_fire_help(lines, out)

    # 静音 fire 内部的 "INFO: Showing help ..." 噪音（fire.core._GetHelpCmd 内部 print）
    import builtins as _bi
    _real_print = _bi.print
    _bi.print = lambda *a, **kw: (_render_fire_info(a, kw) if a and isinstance(a[0], str) and a[0].startswith("INFO: ") else _real_print(*a, **kw))

    result = fire.Fire(cli)
    # 还原 builtins.print（避免污染同进程后续代码）
    _bi.print = _real_print
    if isinstance(result, int):
        sys.exit(result)
    sys.exit(0)


def _render_fire_info(args, kwargs) -> None:
    """替代 fire.core 内部的 print('INFO: Showing help ...')：改成一行 dim 提示。"""
    from rich.console import Console
    c = Console(stderr=True, force_terminal=True, highlight=False)
    c.print(f"[dim]{args[0].rstrip()}[/dim]")


def _render_fire_help(lines, out) -> None:
    """接管 fire.core.Display：用 rich 渲染 help 文本。

    fire help 是 `\\n\\n` 分隔的多段，每段首行是大写标题（NAME/SYNOPSIS/
    DESCRIPTION/POSITIONAL ARGUMENTS/FLAGS/COMMANDS/VALUES/GROUPS/INDEXES/
    NOTES），后跟缩进的 body。策略：
      - 标题行 → 加粗青色
      - 其它行 → 原样输出，保留缩进
      - 整体放进 Panel（标题 = 命令名）
    """
    text = "\n".join(lines).strip("\n")
    from rich.console import Console
    from rich.panel import Panel

    # 保留 fire 输出语义：stderr（fire 默认 Display(... out=sys.stderr)）
    console = Console(file=out, force_terminal=True, highlight=False)

    # 逐段渲染：fire 用 _CreateOutputSection(name, content) = bold(name) +
    # indented(content)，再用 \n\n 拼。每段首行是大写无空格标题（NAME/
    # SYNOPSIS/DESCRIPTION/POSITIONAL ARGUMENTS/FLAGS/COMMANDS/VALUES/
    # GROUPS/INDEXES/NOTES），body 由 fire 内部 SECTION_INDENTATION=4 起缩进。
    # 但 fire 内部 _NewChoicesSection 会再开一段（"    X is one of the
    # following:" + 列表），首行 4 空格起 → 续段。
    #
    # 渲染策略：每段首行 = 大写标题；其余行按原缩进输出（在 fire 已加
    # SECTION_INDENTATION 的基础上 +2 列偏移，让 body 视觉上缩进于标题）。
    chunks = text.split("\n\n")
    for chunk in chunks:
        lines_in = chunk.splitlines()
        if not lines_in:
            continue
        first = lines_in[0]
        first_stripped = first.strip()
        is_title = (
            first_stripped
            and first_stripped == first_stripped.upper()
            and " " not in first_stripped
        )
        if is_title:
            console.print(f"[bold cyan]{first_stripped}[/bold cyan]")
            for line in lines_in[1:]:
                if not line.strip():
                    console.print()
                else:
                    # 保留原缩进（fire SECTION_INDENTATION=4 已加）+ 头部额外 2 空格
                    console.print(f"  {line}")
        else:
            # 续段：原缩进 + 头部 2 空格
            for line in lines_in:
                if not line.strip():
                    console.print()
                else:
                    console.print(f"  {line}")
        console.print()  # 段间空行

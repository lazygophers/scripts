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

    PAGER=- 强制 fire 走 fallback internal pager；为彻底避免任何 pager 行为
    （PAGER=- 不阻止 fire.console.console_pager.Pager），monkey-patch
    fire.console.console_io.More 直接 out.write，不调用 pager。
    """
    from lib.notify import consume_debug, consume_dry_run, consume_no_say

    argv = list(sys.argv)
    argv = consume_dry_run(argv, description=cli.__class__.__doc__ or "")
    argv = consume_debug(argv)
    argv = consume_no_say(argv)
    sys.argv = argv
    os.environ.setdefault("PAGER", "-")

    # 禁用 pager：fire 默认长 help 走 less / 内置 pager，-h 时不便。
    import fire.console.console_io as _cio
    _cio.More = lambda contents, out, prompt=None, check_pager=True: out.write(contents)

    result = fire.Fire(cli)
    if isinstance(result, int):
        sys.exit(result)
    sys.exit(0)

# 加新脚本

两步，互不干扰。`{名}` 表示你的脚本名。

## 1. 写业务逻辑到 `lib/{名}.py`

```python
"""foo: 干啥的（一句话，lazyhelp 目录会引用）。"""


def run() -> int:
    # ... 业务逻辑 ...
    return 0
```

## 2. 把 CLI 写进 `lib/cli/{名}.py`

```python
"""foo — 干啥的"""
from __future__ import annotations

from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.foo import run as do_foo


class FooCli(BaseCli):
    """干啥的"""

    @timed_cli
    def run(self):
        """执行 foo"""
        return do_foo()


def main():
    run_cli(FooCli())
```

## 3. 加薄壳 `bin/{名}`

```python
#!/usr/bin/env python3
"""foo 薄壳入口 — 干啥的"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.cli.foo import main

raise SystemExit(main())
```

```bash
chmod +x bin/{名}
```

## 4. 注册命令、目录与指引

- `pyproject.toml` 的 `[project.scripts]` 加一行 `foo = "lib.cli.foo:main"`。不加这行，装完就没有这个命令，`uvx --from git+https://github.com/lazygophers/scripts foo` 会失败。
- `lib/lazyhelp.py` 的 `TOOLS` 加一行 `"foo": ("分类", "一句话功能")`（分类用 `CATEGORIES_ORDER` 里已有的，没有就新增）。
- 需要 AI 向指引时在 `lib/skills_help.py` 的 `COMMAND_SKILLS` 加 `"foo": ["可直接照抄的示例", ...]`（没有也能跑，会回落到描述）。

`timed_cli` 包裹是强制要求：结束时灰度打印「开始/结束/耗时」到 stderr。

## 同名多入口（多个名字同一逻辑不同参数）

参考 `merge_canary` / `merge_develop` / ...：共用的类只写一份放 `lib/cli/gitwf.py`，再按名字导出一个不带参数的函数，各自把自己的参数显式传进去（`def merge_canary(): _run("merge_canary", "merge", "canary")`）。每个名字都有自己的 `bin/` 薄壳和自己的 `[project.scripts]` 行——不用 symlink，也不靠 argv[0] 猜。

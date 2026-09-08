# 加新脚本

两步，互不干扰。`{名}` 表示你的脚本名。

## 1. 写业务逻辑到 `lib/{名}.py`

```python
"""foo: 干啥的（一句话，lazyhelp 目录会引用）。"""


def run() -> int:
    # ... 业务逻辑 ...
    return 0
```

## 2. 加薄壳 `bin/{名}`

```python
#!/usr/bin/env python3
"""foo — 干啥的（fire 重构）"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.foo import run as do_foo


class FooCli(BaseCli):
    """干啥的"""

    @timed_cli
    def run(self):
        """执行 foo"""
        return do_foo()


if __name__ == "__main__":
    run_cli(FooCli())
```

```bash
chmod +x bin/{名}
```

## 3. 注册目录与指引

- `lib/lazyhelp.py` 的 `TOOLS` 加一行 `"foo": ("分类", "一句话功能")`（分类用 `CATEGORIES_ORDER` 里已有的，没有就新增）。
- 需要 AI 向指引时在 `lib/skills_help.py` 的 `COMMAND_SKILLS` 加 `"foo": ["可直接照抄的示例", ...]`（没有也能跑，会回落到描述）。

`timed_cli` 包裹是强制要求：结束时灰度打印「开始/结束/耗时」到 stderr。

## 同名多入口（多个名字同一逻辑不同参数）

参考 `merge_canary` / `merge_develop` / ...：写一个 `bin/_foo` 统一入口，按 `argv[0]` 的 basename 推断参数，其余名字做成 symlink 指向它。

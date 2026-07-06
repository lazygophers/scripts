# 加新脚本

两步，互不干扰。下文以 `{域}` 表示 `build` / `file` / `git` / `process` / `misc` / `system` 之一，`{名}` 表示你的脚本名。

## 1. 写业务逻辑到 `lib/commands/{域}/{名}.py`

```python
#!/usr/bin/env python3
"""foo - 干啥的。"""
import argparse
import sys


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="foo")
    parser.parse_args(argv[1:])
    # ... 业务逻辑 ...
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

域分类：`build` / `file` / `git` / `process` / `misc` / `system`。新建域记得加 `lib/commands/{域}/__init__.py`。

## 2. 加薄壳 `bin/{名}`

```python
#!/usr/bin/env python3
"""foo 薄壳入口。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.commands.{域}.foo import main

raise SystemExit(main(sys.argv))
```

```bash
chmod +x bin/{名}
```

完成。**无需注册任何字典或列表。**

## 别名脚本（多个名字同一逻辑不同参数）

参考 `merge_canary` / `merge_develop` / ...：在 `lib/commands/git/merge.py` 暴露 `run(target, argv)`，每个薄壳传固定 target：

```python
# bin/merge_canary
from lib.commands.git.merge import run
raise SystemExit(run("canary", sys.argv))
```

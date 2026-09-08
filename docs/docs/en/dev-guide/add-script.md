# Adding a Script

Two steps, independent of each other. `{name}` is your script name.

## 1. Write business logic in `lib/{name}.py`

```python
"""What foo does (one line; quoted by the lazyhelp catalog)."""


def run() -> int:
    # ... business logic ...
    return 0
```

## 2. Add the thin shell `bin/{name}`

```python
#!/usr/bin/env python3
"""foo — what it does (fire refactor)"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.foo import run as do_foo


class FooCli(BaseCli):
    """What it does"""

    @timed_cli
    def run(self):
        """Run foo"""
        return do_foo()


if __name__ == "__main__":
    run_cli(FooCli())
```

```bash
chmod +x bin/{name}
```

## 3. Register catalog & guidance

- Add one line to `TOOLS` in `lib/lazyhelp.py`: `"foo": ("category", "one-line description")` (use an existing `CATEGORIES_ORDER` category, or add one).
- For AI-facing guidance add `"foo": ["copy-pasteable examples", ...]` to `COMMAND_SKILLS` in `lib/skills_help.py` (optional — falls back to the description).

`timed_cli` wrapping is mandatory: it prints a dim start/end/elapsed line to stderr on exit.

## Multiple entry names, same logic

See `merge_canary` / `merge_develop` / ...: write one `bin/_foo` dispatcher that infers the argument from argv[0]'s basename, then symlink the other names to it.

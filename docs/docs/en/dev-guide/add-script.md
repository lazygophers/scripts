# Adding a Script

Two steps, independent of each other. `{name}` is your script name.

## 1. Write business logic in `lib/{name}.py`

```python
"""What foo does (one line; quoted by the lazyhelp catalog)."""


def run() -> int:
    # ... business logic ...
    return 0
```

## 2. Put the CLI in `lib/cli/{name}.py`

```python
"""foo — what it does"""
from __future__ import annotations

from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.foo import run as do_foo


class FooCli(BaseCli):
    """what it does"""

    @timed_cli
    def run(self):
        """Run foo"""
        return do_foo()


def main():
    run_cli(FooCli())
```

## 3. Add the thin shell `bin/{name}`

```python
#!/usr/bin/env python3
"""foo 薄壳入口 — what it does"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.cli.foo import main

raise SystemExit(main())
```

```bash
chmod +x bin/{name}
```

## 4. Register the command, the catalog and the guidance

- Add one line to `[project.scripts]` in `pyproject.toml`: `foo = "lib.cli.foo:main"`. Without it the command does not exist after installation, so `uvx --from git+https://github.com/lazygophers/scripts foo` fails.
- Add one line to `TOOLS` in `lib/lazyhelp.py`: `"foo": ("category", "one-line description")` (use an existing `CATEGORIES_ORDER` category, or add one).
- For AI-facing guidance add `"foo": ["copy-pasteable examples", ...]` to `COMMAND_SKILLS` in `lib/skills_help.py` (optional — falls back to the description).

`timed_cli` wrapping is mandatory: it prints a dim start/end/elapsed line to stderr on exit.

## Multiple entry names, same logic

See `merge_canary` / `merge_develop` / ...: put the shared class in one `lib/cli/gitwf.py`, then export one zero-argument function per name that passes its own parameters (`def merge_canary(): _run("merge_canary", "merge", "canary")`). Each name gets its own `bin/` shell and its own `[project.scripts]` line — never a symlink, and never argv[0] sniffing.

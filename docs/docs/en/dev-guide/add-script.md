# Add a Script

Two steps, independently. Below, `{domain}` is one of `build` / `file` / `git` / `process` / `misc` / `system`, and `{name}` is your script name.

## 1. Put business logic in `lib/commands/{domain}/{name}.py`

```python
#!/usr/bin/env python3
"""foo - what it does."""
import argparse
import sys


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="foo")
    parser.parse_args(argv[1:])
    # ... business logic ...
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

Domains: `build` / `file` / `git` / `process` / `misc` / `system`. New domain needs `lib/commands/{domain}/__init__.py`.

## 2. Add thin entrypoint `bin/{name}`

```python
#!/usr/bin/env python3
"""foo thin entrypoint."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.commands.{domain}.foo import main
from lib.ui import timed

raise SystemExit(timed(main, label="{name}")(sys.argv))
```

```bash
chmod +x bin/{name}
```

Done. **No registry/list to update.**

> The `timed` wrapper is mandatory: every `bin/*` entry wraps its top-level call with it so start/end/elapsed prints to stderr (dim) on exit.

## Alias Scripts (multiple names, same logic, different args)

See `merge_canary` / `merge_develop` / ...: expose `run(target, argv)` in `lib/commands/git/merge.py`, each thin entrypoint passes a fixed target:

```python
# bin/merge_canary
from lib.commands.git.merge import run
from lib.ui import timed
raise SystemExit(timed(run, label="merge_canary")("canary", sys.argv))
```

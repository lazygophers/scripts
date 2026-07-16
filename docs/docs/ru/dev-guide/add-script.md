# Добавить скрипт

Два шага, без помех. Ниже `{домен}` представляет один из `build` / `file` / `git` / `process` / `misc` / `system`, `{имя}` представляет имя вашего скрипта.

## 1. Написать бизнес-логику в `lib/commands/{домен}/{имя}.py`

```python
#!/usr/bin/env python3
"""foo - что делает."""
import argparse
import sys


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="foo")
    parser.parse_args(argv[1:])
    # ... бизнес-логика ...
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

Классификация доменов : `build` / `file` / `git` / `process` / `misc` / `system`. Для новых доменов не забудьте добавить `lib/commands/{домен}/__init__.py`.

## 2. Добавить лёгкий вход `bin/{имя}`

```python
#!/usr/bin/env python3
"""foo Лёгкий вход."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.commands.{домен}.foo import main
from lib.ui import timed

raise SystemExit(timed(main, label="{имя}")(sys.argv))
```

```bash
chmod +x bin/{имя}
```

Готово. **Не требуется регистрация в словарях или списках.**

## Скрипты псевдонимов (несколько имён для той же логики с разными параметрами)

См. `merge_canary` / `merge_develop` / ... : в `lib/commands/git/merge.py` экспортируйте `run(target, argv)`, каждый лёгкий вход передаёт фиксированную цель :

```python
# bin/merge_canary
from lib.commands.git.merge import run
from lib.ui import timed
raise SystemExit(timed(run, label="merge_canary")("canary", sys.argv))
```

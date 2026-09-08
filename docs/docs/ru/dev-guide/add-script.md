# Добавление скрипта

Два шага, независимых друг от друга. `{имя}` — имя вашего скрипта.

## 1. Написать логику в `lib/{имя}.py`

```python
"""What foo does (one line; quoted by the lazyhelp catalog)."""


def run() -> int:
    # ... business logic ...
    return 0
```

## 2. Написать CLI в `lib/cli/{имя}.py`

```python
"""foo — что делает"""
from __future__ import annotations

from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.foo import run as do_foo


class FooCli(BaseCli):
    """что делает"""

    @timed_cli
    def run(self):
        """Запустить foo"""
        return do_foo()


def main():
    run_cli(FooCli())
```

## 3. Добавить обёртку `bin/{имя}`

```python
#!/usr/bin/env python3
"""foo обёртка — что делает"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.cli.foo import main

raise SystemExit(main())
```

```bash
chmod +x bin/{имя}
```

## 4. Зарегистрировать команду, каталог и руководство

- Добавьте строку в `[project.scripts]` в `pyproject.toml`: `foo = "lib.cli.foo:main"`. Без неё после установки команды не существует, и `uvx --from git+https://github.com/lazygophers/scripts foo` падает.
- Добавьте строку в `TOOLS` из `lib/lazyhelp.py`: `"foo": ("категория", "описание в одну строку")` (используйте существующую категорию из `CATEGORIES_ORDER` или создайте новую).
- Для ИИ-руководства добавьте `"foo": ["примеры, которые можно копировать как есть", ...]` в `COMMAND_SKILLS` из `lib/skills_help.py` (опционально — иначе используется описание).

Обёртка `timed_cli` обязательна: печатает в stderr тусклую строку старт/финиш/время при выходе.

## Несколько имён входа, одна логика

См. `merge_canary` / `merge_develop` / ...: общий класс держите в одном `lib/cli/gitwf.py`, а для каждого имени экспортируйте функцию без аргументов, которая явно передаёт свои параметры (`def merge_canary(): _run("merge_canary", "merge", "canary")`). У каждого имени своя обёртка в `bin/` и своя строка в `[project.scripts]` — никаких symlink'ов и никакого разбора argv[0].

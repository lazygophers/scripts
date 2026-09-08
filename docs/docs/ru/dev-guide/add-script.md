# Добавление скрипта

Два шага, независимых друг от друга. `{имя}` — имя вашего скрипта.

## 1. Написать логику в `lib/{имя}.py`

```python
"""What foo does (one line; quoted by the lazyhelp catalog)."""


def run() -> int:
    # ... business logic ...
    return 0
```

## 2. Добавить обёртку `bin/{имя}`

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
chmod +x bin/{имя}
```

## 3. Зарегистрировать в каталоге и руководстве

- Добавьте строку в `TOOLS` из `lib/lazyhelp.py`: `"foo": ("категория", "описание в одну строку")` (используйте существующую категорию из `CATEGORIES_ORDER` или создайте новую).
- Для ИИ-руководства добавьте `"foo": ["примеры, которые можно копировать как есть", ...]` в `COMMAND_SKILLS` из `lib/skills_help.py` (опционально — иначе используется описание).

Обёртка `timed_cli` обязательна: печатает в stderr тусклую строку старт/финиш/время при выходе.

## Несколько имён входа, одна логика

См. `merge_canary` / `merge_develop` / ...: напишите один диспетчер `bin/_foo`, выводящий аргумент из basename argv[0], и сделайте остальные имена symlink'ами на него.

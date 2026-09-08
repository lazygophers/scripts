# Añadir un script

Dos pasos, independientes entre sí. `{nombre}` es el nombre de tu script.

## 1. Escribir la lógica de negocio en `lib/{nombre}.py`

```python
"""What foo does (one line; quoted by the lazyhelp catalog)."""


def run() -> int:
    # ... business logic ...
    return 0
```

## 2. Añadir la entrada fina `bin/{nombre}`

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
chmod +x bin/{nombre}
```

## 3. Registrar catálogo y guía

- Añade una línea a `TOOLS` en `lib/lazyhelp.py`: `"foo": ("categoría", "descripción de una línea")` (usa una categoría existente de `CATEGORIES_ORDER`, o crea una).
- Para la guía IA añade `"foo": ["ejemplos copiables tal cual", ...]` a `COMMAND_SKILLS` en `lib/skills_help.py` (opcional — si no, se usa la descripción).

Envolver con `timed_cli` es obligatorio: imprime en stderr una línea tenue de inicio/fin/duración al salir.

## Varios nombres de entrada, misma lógica

Véase `merge_canary` / `merge_develop` / ...: escribe un dispatcher único `bin/_foo` que deduzca el argumento del basename de argv[0], y crea symlinks para los demás nombres.

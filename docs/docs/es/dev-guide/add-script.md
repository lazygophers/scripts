# Añadir un script

Dos pasos, independientes entre sí. `{nombre}` es el nombre de tu script.

## 1. Escribir la lógica de negocio en `lib/{nombre}.py`

```python
"""What foo does (one line; quoted by the lazyhelp catalog)."""


def run() -> int:
    # ... business logic ...
    return 0
```

## 2. Escribir la CLI en `lib/cli/{nombre}.py`

```python
"""foo — qué hace"""
from __future__ import annotations

from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.foo import run as do_foo


class FooCli(BaseCli):
    """qué hace"""

    @timed_cli
    def run(self):
        """Ejecutar foo"""
        return do_foo()


def main():
    run_cli(FooCli())
```

## 3. Añadir la entrada fina `bin/{nombre}`

```python
#!/usr/bin/env python3
"""foo entrada fina — qué hace"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.cli.foo import main

raise SystemExit(main())
```

```bash
chmod +x bin/{nombre}
```

## 4. Registrar el comando, el catálogo y la guía

- Añade una línea a `[project.scripts]` en `pyproject.toml`: `foo = "lib.cli.foo:main"`. Sin ella el comando no existe tras la instalación y `uvx --from git+https://github.com/lazygophers/scripts foo` falla.
- Añade una línea a `TOOLS` en `lib/lazyhelp.py`: `"foo": ("categoría", "descripción de una línea")` (usa una categoría existente de `CATEGORIES_ORDER`, o crea una).
- Para la guía IA añade `"foo": ["ejemplos copiables tal cual", ...]` a `COMMAND_SKILLS` en `lib/skills_help.py` (opcional — si no, se usa la descripción).

Envolver con `timed_cli` es obligatorio: imprime en stderr una línea tenue de inicio/fin/duración al salir.

## Varios nombres de entrada, misma lógica

Véase `merge_canary` / `merge_develop` / ...: pon la clase compartida en un único `lib/cli/gitwf.py` y exporta una función sin argumentos por nombre, que pase sus propios parámetros (`def merge_canary(): _run("merge_canary", "merge", "canary")`). Cada nombre tiene su propia entrada en `bin/` y su propia línea en `[project.scripts]`: nunca un symlink ni deducción por argv[0].

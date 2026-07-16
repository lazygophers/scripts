# Añadir un script

Dos pasos, sin interferencia. A continuación `{dominio}` representa uno de `build` / `file` / `git` / `process` / `misc` / `system`, `{nombre}` representa su nombre de script.

## 1. Escribir la lógica de negocio en `lib/commands/{dominio}/{nombre}.py`

```python
#!/usr/bin/env python3
"""foo - lo que hace."""
import argparse
import sys


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="foo")
    parser.parse_args(argv[1:])
    # ... lógica de negocio ...
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

Clasificación de dominios : `build` / `file` / `git` / `process` / `misc` / `system`. Para dominios nuevos, no olvide añadir `lib/commands/{dominio}/__init__.py`.

## 2. Añadir la entrada ligera `bin/{nombre}`

```python
#!/usr/bin/env python3
"""foo Entrada ligera."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.commands.{dominio}.foo import main
from lib.ui import timed

raise SystemExit(timed(main, label="{nombre}")(sys.argv))
```

```bash
chmod +x bin/{nombre}
```

Terminado. **No es necesario registrar ningún diccionario o lista.**


> El envoltorio `timed` es obligatorio: cada entrada `bin/*` envuelve su llamada de nivel superior con él para que inicio/fin/duración se imprima en stderr (tenue) al salir.
## Scripts de alias (múltiples nombres para la misma lógica con diferentes parámetros)

Referencia `merge_canary` / `merge_develop` / ... : en `lib/commands/git/merge.py` exponga `run(target, argv)`, cada entrada ligera transmite un objetivo fijo :

```python
# bin/merge_canary
from lib.commands.git.merge import run
from lib.ui import timed
raise SystemExit(timed(run, label="merge_canary")("canary", sys.argv))
```

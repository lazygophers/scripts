# Ajouter un script

Deux étapes, sans interférence. Ci-dessous `{domaine}` représente l'un de `build` / `file` / `git` / `process` / `misc` / `system`, `{nom}` représente votre nom de script.

## 1. Écrire la logique métier dans `lib/commands/{domaine}/{nom}.py`

```python
#!/usr/bin/env python3
"""foo - ce qu'il fait."""
import argparse
import sys


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="foo")
    parser.parse_args(argv[1:])
    # ... logique métier ...
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

Classification des domaines : `build` / `file` / `git` / `process` / `misc` / `system`. Pour les nouveaux domaines, n'oubliez pas d'ajouter `lib/commands/{domaine}/__init__.py`.

## 2. Ajouter l'entrée légère `bin/{nom}`

```python
#!/usr/bin/env python3
"""foo Entrée légère."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.commands.{domaine}.foo import main

raise SystemExit(main(sys.argv))
```

```bash
chmod +x bin/{nom}
```

Terminé. **Aucune inscription dans un dictionnaire ou une liste n'est nécessaire.**

## Scripts d'alias (plusieurs noms pour la même logique avec différents paramètres)

Référence `merge_canary` / `merge_develop` / ... : dans `lib/commands/git/merge.py` exposez `run(target, argv)`, chaque entrée légère transmet une cible fixe :

```python
# bin/merge_canary
from lib.commands.git.merge import run
raise SystemExit(run("canary", sys.argv))
```

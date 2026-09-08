# Ajouter un script

Deux étapes, indépendantes l'une de l'autre. `{nom}` est le nom de votre script.

## 1. Écrire la logique métier dans `lib/{nom}.py`

```python
"""What foo does (one line; quoted by the lazyhelp catalog)."""


def run() -> int:
    # ... business logic ...
    return 0
```

## 2. Ajouter l'entrée fine `bin/{nom}`

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
chmod +x bin/{nom}
```

## 3. Enregistrer catalogue & guidance

- Ajoutez une ligne dans `TOOLS` de `lib/lazyhelp.py` : `"foo": ("catégorie", "description en une ligne")` (utilisez une catégorie existante de `CATEGORIES_ORDER`, sinon créez-en une).
- Pour la guidance IA, ajoutez `"foo": ["exemples copiables tels quels", ...]` dans `COMMAND_SKILLS` de `lib/skills_help.py` (optionnel — à défaut, la description est utilisée).

L'enveloppement `timed_cli` est obligatoire : il affiche en stderr une ligne grise début/fin/durée à la sortie.

## Plusieurs noms d'entrée, même logique

Voir `merge_canary` / `merge_develop` / ... : écrivez un dispatcher unique `bin/_foo` qui déduit l'argument du basename de argv[0], puis créez des symlinks pour les autres noms.

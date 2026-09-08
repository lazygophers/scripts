# Ajouter un script

Deux étapes, indépendantes l'une de l'autre. `{nom}` est le nom de votre script.

## 1. Écrire la logique métier dans `lib/{nom}.py`

```python
"""What foo does (one line; quoted by the lazyhelp catalog)."""


def run() -> int:
    # ... business logic ...
    return 0
```

## 2. Écrire la CLI dans `lib/cli/{nom}.py`

```python
"""foo — ce que ça fait"""
from __future__ import annotations

from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.foo import run as do_foo


class FooCli(BaseCli):
    """ce que ça fait"""

    @timed_cli
    def run(self):
        """Exécuter foo"""
        return do_foo()


def main():
    run_cli(FooCli())
```

## 3. Ajouter l'entrée fine `bin/{nom}`

```python
#!/usr/bin/env python3
"""foo entrée fine — ce que ça fait"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.cli.foo import main

raise SystemExit(main())
```

```bash
chmod +x bin/{nom}
```

## 4. Enregistrer la commande, le catalogue et la guidance

- Ajoutez une ligne à `[project.scripts]` dans `pyproject.toml` : `foo = "lib.cli.foo:main"`. Sans elle, la commande n'existe pas après installation et `uvx --from git+https://github.com/lazygophers/scripts foo` échoue.
- Ajoutez une ligne dans `TOOLS` de `lib/lazyhelp.py` : `"foo": ("catégorie", "description en une ligne")` (utilisez une catégorie existante de `CATEGORIES_ORDER`, sinon créez-en une).
- Pour la guidance IA, ajoutez `"foo": ["exemples copiables tels quels", ...]` dans `COMMAND_SKILLS` de `lib/skills_help.py` (optionnel — à défaut, la description est utilisée).

L'enveloppement `timed_cli` est obligatoire : il affiche en stderr une ligne grise début/fin/durée à la sortie.

## Plusieurs noms d'entrée, même logique

Voir `merge_canary` / `merge_develop` / ... : mettez la classe partagée dans un seul `lib/cli/gitwf.py`, puis exportez une fonction sans argument par nom, qui passe ses propres paramètres (`def merge_canary(): _run("merge_canary", "merge", "canary")`). Chaque nom a sa propre entrée `bin/` et sa propre ligne `[project.scripts]` — jamais de symlink, jamais de déduction via argv[0].

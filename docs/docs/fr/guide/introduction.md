# Introduction

`scripts` est une collection d'outils d'efficacité — des raccourcis pour les tâches courantes de dev et d'ops. `bin/` ne contient que des entrées fines ; les implémentations sont dans `lib/cli/` et les capacités partagées dans `lib/`.

## Points forts

- **Entrées fines** : chaque script de `bin/` est la même coquille (correction du path + `from lib.cli.<module> import main`) — aucune logique métier, aucun symlink ; l'implémentation est `lib/cli/<nom>.py` et les capacités partagées sont les modules plats de `lib/`.
- **Exécution distante sans installation** : toutes les commandes sont déclarées dans `[project.scripts]`, donc `uvx git+https://github.com/lazygophers/scripts` s'exécute sans cloner.
- **Classification par usage** : Flux Git / Collaboration Git / Build & Vérification / Données & Réseau / Recherche Web / Processus & Exécution / Fichiers & Système — une page via `lazyhelp`.
- **Opérations par lot** : `merge_*` / `push_*` / `switch_branch` / `sync_master` couvrent dépôt seul et lot multi-dépôts.
- **Sécurité d'abord** : auto-exclusion de la gestion de processus, vérification de propreté et rollback avant les opérations Git.

## Démarrage rapide

Exécuter une fois, sans rien installer :

```bash
uvx git+https://github.com/lazygophers/scripts                     # lister tous les outils
uvx --from git+https://github.com/lazygophers/scripts checkwork    # en exécuter un seul
```

Les garder sur cette machine :

```bash
./bin/inject            # injecte le bin/ cloné dans le PATH du shell
```

Redémarrez le shell ensuite, puis appelez `checkwork` / `merge_canary` / ... depuis n'importe quel répertoire.

Voir [Scripts](./scripts.md) et le dépôt GitHub [lazygophers/scripts](https://github.com/lazygophers/scripts).

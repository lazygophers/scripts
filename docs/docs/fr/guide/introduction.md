# Introduction

`scripts` est une collection d'outils d'efficacité — des raccourcis pour les tâches courantes de dev et d'ops. Entrées fines Bash/Python, logique métier dans `lib/`.

## Points forts

- **Entrées fines** : les scripts de `bin/` sont 3 lignes de hack de path + import ; toute la logique est dans les modules plats de `lib/`.
- **Classification par usage** : Flux Git / Collaboration Git / Build & Vérification / Données & Réseau / Recherche Web / Processus & Exécution / Fichiers & Système — une page via `lazyhelp`.
- **Opérations par lot** : `merge_*` / `push_*` / `switch_branch` / `sync_master` couvrent dépôt seul et lot multi-dépôts.
- **Sécurité d'abord** : auto-exclusion de la gestion de processus, vérification de propreté et rollback avant les opérations Git.

## Démarrage rapide

```bash
./bin/inject            # injecte bin/ dans le PATH du shell
```

Redémarrez le shell ensuite, puis appelez `checkwork` / `merge_canary` / ... depuis n'importe quel répertoire.

Voir [Scripts](./scripts.md) et le dépôt GitHub [lazygophers/scripts](https://github.com/lazygophers/scripts).

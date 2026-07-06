# Introduction

`scripts` est une collection d'utilitaires d'efficacité de développement — des raccourcis rapides pour les tâches courantes de développement et d'exploitation. Entrées légères Bash/Python, avec logique principale établie dans `lib/`.

## Points forts

- **Entrées légères** : les scripts sous `bin/` ne font que 3 lignes de hack de chemin + import ; toute la logique métier vit sous `lib/commands/`.
- **Classés par domaine** : `build` / `file` / `git` / `process` / `misc` / `system`, mutuellement isolés.
- **Opérations par lots** : `merge_*` / `push_*` / `switch_branch` / `sync_master` couvrent à la fois le dépôt unique et les lots multi-dépôts.
- **Sécurité d'abord** : auto-exclusion de la gestion des processus, vérifications de propreté de l'arbre de travail et retour avant les opérations Git.

## Démarrage rapide

```bash
./bin/inject            # injecter bin/ dans le PATH shell
```

Redémarrez votre shell ensuite, puis appelez `checkwork` / `merge_canary` / ... depuis n'importe quel répertoire.

Voir [Scripts](./scripts.md) et le dépôt GitHub [lazygophers/scripts](https://github.com/lazygophers/scripts).

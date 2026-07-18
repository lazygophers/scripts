# scripts

[简体中文](README.md) | [English](README.en.md) | [Français](README.fr.md) | [Español](README.es.md) | [Русский](README.ru.md) | [العربية](README.ar.md)

Collection d'utilitaires d'efficacité de développement — divers raccourcis de scripts. Entrées légères Bash/Python, logique principale dans `lib/`.

---

## Installation : Injecter bin/ dans PATH

```bash
./bin/inject            # Générer ~/.scripts.sh et source vers tous les rc (~/.bashrc / ~/.zshrc / ~/.profile / ~/.bash_profile)
./bin/inject --show     # Prévisualiser le contenu à écrire
./bin/inject --uninstall  # Désinstaller
```

inject est idempotent : réexécuter ne dupliquera pas. Après achèvement, redémarrer le shell ou `source ~/.zshrc`, puis appeler `checkwork` / `merge_canary` / ... depuis n'importe quel répertoire.

---

## Fonctionnalités des scripts

| Script             | Fonction                                                         | Exemple                          |
| :--------------- | :----------------------------------------------------------- | :---------------------------- |
| `checkwork`      | Vérification automatisée de la compilation + notifications vocales            | `checkwork`                   |
| `cpd`            | Copie profonde (par défaut ajouter/mettre à jour seulement ; `-f` supprimer les excédents)        | `cpd src/* dest/`             |
| `kk`             | Terminer les processus par nom                                             | `kk nginx`                    |
| `kkp`            | Terminer les processus par port                                                | `kkp 8080`                    |
| `n`              | Diffusion vocale macOS (`say`)                                       | `n "construction terminée"`                |
| `loop`           | Exécuter des commandes en boucle, suivre succès/échec                                  | `loop 10 curl url`            |
| `merge_canary`   | Fusionner la branche actuelle → canary, rester sur canary                           | `merge_canary [--dry-run]`     |
| `merge_develop`  | Fusionner la branche actuelle → develop, rester sur develop                         | `merge_develop`                |
| `merge_master`     | Fusionner la branche actuelle → branche principale (auto-detect master/main), rester sur cible                        | `merge_master`                   |
| `merge_test`     | Fusionner la branche actuelle → test, rester sur test                               | `merge_test`                   |
| `push_canary`    | Fusionner la branche actuelle → canary, pousser puis revenir                      | `push_canary [--stay]`         |
| `push_develop` / `push_master` / `push_test` | Idem, cibles develop / branche principale (auto-detect) / test respectivement      |                               |
| `push_*` (batch)  | Lors de l'exécution de push_* dans répertoire non-git, automatiquement par lots : scanner les dépôts Git des sous-répertoires et pousser un par un | `push_canary [--dry-run]` |
| `switch_branch`  | Basculer des branches par lots (crée depuis branche par défaut (auto-détectée) si inexistant)                 | `switch_branch <branch>`      |
| `sync_master`    | Synchroniser master par lots = `sync_branch master`                                              | `sync_master`                 |
| `sync_branch`    | Synchroniser par lots branche courante (ou donnée) vers origin/<branch> | `sync_branch [branch] [--force]` |
| `delete_branch` | Supprimer branche locale (repo unique; lot si hors dir git) | `delete_branch <name> [--force] [-y]` |
| `delete_branch_remote` | Supprimer branche distante (repo unique; lot si hors dir git) | `delete_branch_remote <name> [--remote <r>] [-y]` |
| `fetch_all`  | Récupérer par lots tous les dépôts Git                                     | `fetch_all`               |
| `list_branch`  | Lister les branches locales (repo unique ou scan tous les dépôts, noms dupliqués cross-repo marqués ⟱ ; groupé par dépôt) | `list_branch` |
| `unsleep`        | Empêcher la mise en veille macOS caffeinate                                      | `unsleep -t 3600`             |
| `reindex`        | Réindexer le projet (local-only, .gitignore)                        | `reindex`                     |
| `inject`         | Injecter bin/ dans le PATH shell                                      | `inject`                      |

> **Notes de migration (anciens noms supprimés)** : `mergec`/`mergedev`/`mergem`/`merget` → `merge_canary`/`merge_develop`/`merge_master`/`merge_test` ; `pushc`/`pushdev`/`pushm`/`pusht` → `push_canary`/`push_develop`/`push_master`/`push_test` ; `pushc_all` a fusionné dans `push_*` (exécuter dans répertoire non-git pour auto batch, exécution auto sans confirmation, `--dry-run` prévisualiser).

> **Variables d'environnement** : `BATCH_CONCURRENCY` contrôle l'opération par lots (`push_*` / `switch_branch` / `sync_branch` / `sync_master`) limite de concurrence parallèle, par défaut `4`. Exemple : `BATCH_CONCURRENCY=8 push_canary`.

---

## Documentation

Site complet de documentation : https://lazygophers.github.io/scripts/

# Scripts

## Installation

```bash
./bin/inject            # générer ~/.scripts.sh et sourcer vers tous les rc
./bin/inject --show     # prévisualiser le contenu à écrire
./bin/inject --uninstall  # désinstaller
```

inject est idempotent : réexécuter n'ajoutera pas de doublons. Après redémarrage du shell ou `source ~/.zshrc`, vous pouvez appeler directement depuis n'importe quel répertoire.

## Tableau des fonctionnalités

| Script             | Fonction                                                         | Exemple                          |
| :--------------- | :----------------------------------------------------------- | :---------------------------- |
| `checkwork`      | Vérification automatisée de la compilation + notifications vocales            | `checkwork`                   |
| `cpd`            | Copie profonde par défaut (ajout/mise à jour seulement ; `-f` supprime les excédents)        | `cpd src/* dest/`             |
| `kk`             | Terminer les processus par nom                                              | `kk nginx`                    |
| `kkp`            | Terminer les processus par port                                                | `kkp 8080`                    |
| `n`              | Diffusion vocale macOS (`say`)                                       | `n "construction terminée"`                |
| `loop`           | Exécuter des commandes en boucle, suivre succès/échec                                  | `loop 10 curl url`            |
| `merge_canary`   | Fusionner la branche actuelle → canary, rester sur canary                           | `merge_canary [--dry-run]`     |
| `merge_develop`  | Fusionner la branche actuelle → develop, rester sur develop                         | `merge_develop`                |
| `merge_auto`     | Fusionner la branche actuelle → branche par défaut distante, rester sur cible                        | `merge_auto`                   |
| `merge_test`     | Fusionner la branche actuelle → test, rester sur test                               | `merge_test`                   |
| `push_canary`    | Fusionner la branche actuelle → canary, pousser puis revenir à la branche d'origine                      | `push_canary [--stay]`         |
| `push_develop` / `push_auto` / `push_test` | Idem, cibles develop / distant par défaut / test respectivement      |                               |
| `push_*` (batch)  | Lors de l'exécution de push_* dans un répertoire non-git, automatiquement par lots : scanner les dépôts GitLab des sous-répertoires et pousser un par un | `push_canary [--dry-run]` |
| `switch_branch`  | Basculer des branches par lots (crée depuis origin/master si inexistant)                 | `switch_branch <branch>`      |
| `sync_master`    | Synchroniser master par lots                                              | `sync_master`                 |
| `sync_branch`    | Synchroniser par lots branche courante (ou donnée) vers origin/<branch>                             | `sync_branch [branch] [--force]` |
| `fetch_all`  | Récupérer par lots tous les dépôts Git                                     | `fetch_all`               |
| `unsleep`        | Empêcher la mise en veille macOS caffeinate                                      | `unsleep -t 3600`             |
| `reindex`        | Réindexer le projet (local-only, .gitignore)                        | `reindex`                     |
| `inject`         | Injecter bin/ dans le PATH shell                                      | `inject`                      |

## Notes de migration (anciens noms supprimés)

- `mergec` / `mergedev` / `mergem` / `merget` → `merge_canary` / `merge_develop` / `merge_auto` / `merge_test`
- `pushc` / `pushdev` / `pushm` / `pusht` → `push_canary` / `push_develop` / `push_auto` / `push_test`
- `pushc_all` a été fusionné dans `push_*` : exécuter dans un répertoire non-git déclenche automatiquement le mode par lots, exécution automatique sans confirmation, `--dry-run` pour prévisualiser.

## Variables d'environnement

- `BATCH_CONCURRENCY` : limite supérieure de parallélisme pour les opérations par lots (`push_*` / `switch_branch` / `sync_branch` / `sync_master`), par défaut `4`. Exemple : `BATCH_CONCURRENCY=8 push_canary`.

## Dépendances d'environnement

- **Python 3.10+** (entrée légère et logique principale)
- **Git** (merge_* / push_* / switch_branch / sync_master / fetch_all)
- **macOS** (`n` utilise `say`, `unsleep` utilise `caffeinate`)
- **rich** (embellissement de sortie, `pip install rich`)
- **pgrep / ps / lsof / kill** (kk / kkp)

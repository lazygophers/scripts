# Scripts

## Sans installation : exécuter directement depuis GitHub

```bash
uvx git+https://github.com/lazygophers/scripts                     # lister tous les outils (comme lazyhelp)
uvx --from git+https://github.com/lazygophers/scripts checkwork    # en exécuter un seul
uv tool install git+https://github.com/lazygophers/scripts         # installation permanente, commandes dans le PATH
```

`uvx` est fourni avec [uv](https://docs.astral.sh/uv/) : il télécharge un outil, l'exécute une fois et ne laisse rien — aucun clone nécessaire. Sans nom de commande, il lance le point d'entrée `scripts`, qui est `lazyhelp` ; avec `--from`, uv exige un nom de commande explicite.

## Installation

```bash
./bin/inject            # générer ~/.scripts.sh et sourcer vers tous les rc
./bin/inject show     # prévisualiser le contenu à écrire
./bin/inject uninstall  # désinstaller
```

inject est idempotent : réexécuter n'ajoutera pas de doublons. Après redémarrage du shell ou `source ~/.zshrc`, vous pouvez appeler directement depuis n'importe quel répertoire.

## Tableau des fonctionnalités

Sept catégories par usage. Index terminal : `lazyhelp` ; usage complet : `<outil> --help` ; guide IA : `<outil> --skills`.

### Flux Git

| Script | Fonction | Exemple |
| :--- | :--- | :--- |
| `merge_canary` | Fusionne la branche courante → canary, reste sur canary | `merge_canary [--dry-run]` |
| `merge_develop` / `merge_dev` / `merge_test` | Idem, cibles develop / dev / test | `merge_develop` |
| `merge_master` | Fusionne la branche courante → branche principale (master/main auto-détecté), reste sur la cible | `merge_master` |
| `merge_branch` | Fusionne la branche courante → branche donnée (nom de branche obligatoire en 1er arg) | `merge_branch feature/x` |
| `push_canary` | Fusionne la branche courante → canary, pousse puis revient | `push_canary [--stay]` |
| `push_develop` / `push_dev` / `push_test` | Idem, cibles develop / dev / test |  |
| `push_master` | Idem, cible la branche principale auto-détectée |  |
| `push_branch` | Pousse la branche courante vers la branche donnée (nom obligatoire en 1er arg) | `push_branch feature/x` |
| `switch_branch` | Change de branche en lot (crée depuis la principale si absente) | `switch_branch <branch>` |
| `sync_branch` | Synchronise en lot la branche courante (ou donnée) vers origin/<branch> | `sync_branch [branch] [--force]` |
| `sync_master` | Synchronise en lot la branche principale (auto-détectée) | `sync_master` |
| `delete_branch` | Supprime une branche locale (dépôt seul ; lot sinon) | `delete_branch <name> [--force] [-y]` |
| `delete_branch_remote` | Supprime une branche distante (dépôt seul ; lot sinon) | `delete_branch_remote <name> [--remote <r>] [-y]` |

> Exécutés hors dépôt git, ces commandes passent en mode lot : scan des sous-dépôts git et exécution un par un.

### Collaboration Git

| Script | Fonction | Exemple |
| :--- | :--- | :--- |
| `commit` | Commit automatique (claude génère le message) | `commit` |
| `mr` | Crée PR/MR automatiquement (claude génère titre/corps, draft par défaut) | `mr [base]` |
| `issue` | Crée un Issue automatiquement (claude génère titre/corps) | `issue` |
| `squash_pr` | Écrase source en un commit → ouvre PR via mr | `squash_pr [source] <target>` |
| `fetch_all` | Fetch en lot de tous les dépôts Git | `fetch_all` |
| `list_branch` | Liste les branches locales (dépôt seul ou scan global, doublons ⟱) | `list_branch` |

### Build & Vérification

| Script | Fonction | Exemple |
| :--- | :--- | :--- |
| `checkwork` | Porte de build multi-langage avant push (Go/Rust/Python/Java/Node) + annonce vocale | `checkwork` |
| `check_ai` | Test de connectivité des endpoints d'API IA (POST vide) | `check_ai` |
| `cicd` | Interroge le CI/CD de la branche courante, affiche le résultat final | `cicd` |

### Données & Réseau

| Script | Fonction | Exemple |
| :--- | :--- | :--- |
| `archery` | CLI Archery SQL (requêtes / workflow de mise en ligne, login par domaine) | `archery query execute 'select 1' --instance-name prod --db-name orders` |
| `grafana` | CLI API HTTP Grafana (login par domaine) | `grafana health` |
| `ovpn` | Client OpenVPN (identifiants & TOTP auto, split tunneling) | `ovpn connect` |
| `vpn-prio` | Ajuste la priorité des services réseau macOS (baisse la route OpenVPN) | `vpn-prio --help` |
| `ipinfo` | IP LAN + type de réseau (détection hotspot) | `ipinfo` |
| `disable-ipv6` / `enable-ipv6` | Désactive/active IPv6 sur tous les services réseau (sudo requis) | `sudo disable-ipv6` |

### Recherche Web

| Script | Fonction | Exemple |
| :--- | :--- | :--- |
| `websearch` | Recherche web multi-moteurs (sans clé, parallèle, dédoublonnée par URL) | `websearch rust async` |
| `webgrab` | Page web → Markdown (contournement anti-bot + rendu Playwright + 34 sites + login persistant) | `webgrab https://example.com` |

### Processus & Exécution

| Script | Fonction | Exemple |
| :--- | :--- | :--- |
| `kk` | Termine les processus par nom | `kk nginx` |
| `kkp` | Termine les processus par port | `kkp 8080` |
| `loop` | Exécute une commande en boucle, suit succès/échec | `loop 10 curl url` |
| `unsleep` | Anti-veille macOS (caffeinate) | `unsleep timed 2h` |

### Fichiers & Système

| Script | Fonction | Exemple |
| :--- | :--- | :--- |
| `cpd` | Copie profonde (ajout/maj par défaut ; `-f` supprime les extras) | `cpd src/* dest/` |
| `n` | Annonce vocale macOS (`say`) | `n "build complete"` |
| `inject` | Injecte bin/ dans le PATH du shell | `inject` |
| `graphwatch` | Démon graphify : rebuild automatique du graphe de connaissances | `graphwatch add <dir>` |
| `lazyhelp` | Index terminal des outils + transfert `--help` | `lazyhelp help <tool>` |

## Notes de migration (anciens noms supprimés)

- `mergec` / `mergedev` / `mergem` / `merget` → `merge_canary` / `merge_develop` / `merge_master` / `merge_test`
- `pushc` / `pushdev` / `pushm` / `pusht` → `push_canary` / `push_develop` / `push_master` / `push_test`
- `pushc_all` a été fusionné dans `push_*` : exécuter dans un répertoire non-git déclenche automatiquement le mode par lots, exécution automatique sans confirmation, `--dry-run` pour prévisualiser.

## Variables d'environnement

- `BATCH_CONCURRENCY` : limite supérieure de parallélisme pour les opérations par lots (`push_*` / `switch_branch` / `sync_branch` / `sync_master`), par défaut `4`. Exemple : `BATCH_CONCURRENCY=8 push_canary`.

## Dépendances d'environnement

- **Python 3.10+** (entrée légère et logique principale)
- **Git** (merge_* / push_* / switch_branch / sync_master / fetch_all / delete_branch)
- **macOS** (`n` utilise `say`, `unsleep` utilise `caffeinate`)
- **rich** (embellissement de sortie, `pip install rich`)
- **pgrep / ps / lsof / kill** (kk / kkp)

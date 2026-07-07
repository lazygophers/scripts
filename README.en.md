# scripts

[简体中文](README.md) | [English](README.en.md) | [Français](README.fr.md) | [Español](README.es.md) | [Русский](README.ru.md) | [العربية](README.ar.md)

A collection of development efficiency utilities — various script shortcuts. Bash/Python mixed thin shell entrypoints, core logic in `lib/`.

---

## Installation: Inject bin/ into PATH

```bash
./bin/inject            # Generate ~/.scripts.sh and source to all rc (~/.bashrc / ~/.zshrc / ~/.profile / ~/.bash_profile)
./bin/inject --show     # Preview what will be written
./bin/inject --uninstall  # Uninstall
```

inject is idempotent: rerunning won't duplicate. After completion, restart shell or `source ~/.zshrc`, then call `checkwork` / `merge_canary` / ... from any directory.

---

## Script Features

| Script             | Function                                                         | Example                          |
| :--------------- | :----------------------------------------------------------- | :---------------------------- |
| `checkwork`      | Automated build checking + voice notifications            | `checkwork`                   |
| `cpd`            | Deep copy (default only add/update; `-f` delete target extras)        | `cpd src/* dest/`             |
| `kk`             | Kill processes by name                                             | `kk nginx`                    |
| `kkp`            | Kill processes by port                                               | `kkp 8080`                    |
| `n`              | macOS voice broadcast (`say`)                                       | `n "build complete"`                |
| `loop`           | Loop command execution, track success/failure                                  | `loop 10 curl url`            |
| `merge_canary`   | Merge current branch → canary, stay on canary                           | `merge_canary [--dry-run]`     |
| `merge_develop`  | Merge current branch → develop, stay on develop                         | `merge_develop`                |
| `merge_auto`     | Merge current branch → remote default branch, stay on target                        | `merge_auto`                   |
| `merge_test`     | Merge current branch → test, stay on test                               | `merge_test`                   |
| `push_canary`    | Merge current branch → canary, push then switch back                      | `push_canary [--stay]`         |
| `push_develop` / `push_auto` / `push_test` | Same, targets are develop / remote default / test respectively      |                               |
| `push_*` (batch)  | When executing push_* in non-git directory, automatically batch: scan subdirectory Git repos and push one by one | `push_canary [--dry-run]` |
| `switch_branch`  | Batch switch branches (create from origin/master if not exists)                 | `switch_branch <branch>`      |
| `sync_master`    | Batch sync master = `sync_branch master`                                              | `sync_master`                 |
| `sync_branch`    | Batch sync current (or given) branch to origin/<branch>      | `sync_branch [branch] [--force]` |
| `delete_branch` | Delete local branch (single-repo; batch if not in git dir) | `delete_branch <name> [--force] [-y]` |
| `delete_branch_remote` | Delete remote branch (single-repo; batch if not in git dir) | `delete_branch_remote <name> [--remote <r>] [-y]` |
| `fetch_all`  | Batch fetch all Git repositories                                     | `fetch_all`               |
| `unsleep`        | macOS caffeinate anti-idle                                      | `unsleep -t 3600`             |
| `reindex`        | Project re-index (local-only, .gitignore)                        | `reindex`                     |
| `inject`         | Inject bin/ into shell PATH                                      | `inject`                      |

> **Migration Notes (old names removed)**: `mergec`/`mergedev`/`mergem`/`merget` → `merge_canary`/`merge_develop`/`merge_auto`/`merge_test`; `pushc`/`pushdev`/`pushm`/`pusht` → `push_canary`/`push_develop`/`push_auto`/`push_test`; `pushc_all` merged into `push_*` (execute in non-git directory for auto batch, auto execute without confirmation, `--dry-run` preview).

> **Environment Variables**: `BATCH_CONCURRENCY` controls batch operation (`push_*` / `switch_branch` / `sync_branch` / `sync_master`) parallel concurrency limit, defaults to `4`. Example: `BATCH_CONCURRENCY=8 push_canary`.

---

## Documentation

Full documentation site: https://lazygophers.github.io/scripts/

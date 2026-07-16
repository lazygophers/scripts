# Scripts

## Install

```bash
./bin/inject            # generate ~/.scripts.sh and source it into all rc files
./bin/inject --show     # preview what will be written
./bin/inject --uninstall  # uninstall
```

inject is idempotent — re-running won't duplicate entries. Restart your shell or `source ~/.zshrc` afterwards to call commands from any directory.

## Feature Table

| Script           | What it does                                                 | Example                       |
| :--------------- | :----------------------------------------------------------- | :---------------------------- |
| `checkwork`      | Automated build check + voice notification                   | `checkwork`                   |
| `cpd`            | Deep overlay copy (default only adds/updates; `-f` deletes extra files in dest) | `cpd src/* dest/`        |
| `kk`             | Kill processes by name                                       | `kk nginx`                    |
| `kkp`            | Kill processes by port                                       | `kkp 8080`                    |
| `n`              | macOS voice broadcast (`say`)                                | `n "build done"`              |
| `loop`           | Loop a command, tracking success/failure                     | `loop 10 curl url`            |
| `merge_canary`   | Merge current branch → canary, stay on canary                | `merge_canary [--dry-run]`    |
| `merge_develop`  | Merge current branch → develop, stay on develop              | `merge_develop`               |
| `merge_master`     | Merge current branch → main (auto-detect master/main), stay on target        | `merge_master`                  |
| `merge_test`     | Merge current branch → test, stay on test                    | `merge_test`                  |
| `push_canary`    | Merge current branch → canary, push, switch back             | `push_canary [--stay]`        |
| `push_develop` / `push_master` / `push_test` | Same as above, target = develop / main (auto-detect) / test |                |
| `push_*` (batch) | Running push_* in a non-git dir auto-batches: scans subdirs for Git repos and pushes each | `push_canary [--dry-run]` |
| `switch_branch`  | Batch switch branch (create from default branch (auto-detected) if missing)   | `switch_branch <branch>`      |
| `sync_master`    | Batch sync master                                            | `sync_master`                 |
| `sync_branch`    | Batch sync current (or given) branch to origin/<branch>                             | `sync_branch [branch] [--force]` |
| `delete_branch` | Delete local branch (single/batch) | `delete_branch <name> [--force] [-y]` |
| `delete_branch_remote` | Delete remote branch (single/batch) | `delete_branch_remote <name> [--remote <r>] [-y]` |
| `fetch_all`  | Batch fetch all Git repos                                    | `fetch_all`               |
| `unsleep`        | macOS caffeinate anti-idle                                   | `unsleep -t 3600`             |
| `reindex`        | Reindex project (local-only, .gitignore)                     | `reindex`                     |
| `inject`         | Inject bin/ into shell PATH                                  | `inject`                      |

## Migration Notes (old names removed)

- `mergec` / `mergedev` / `mergem` / `merget` → `merge_canary` / `merge_develop` / `merge_master` / `merge_test`
- `pushc` / `pushdev` / `pushm` / `pusht` → `push_canary` / `push_develop` / `push_master` / `push_test`
- `pushc_all` is merged into `push_*`: running in a non-git dir auto-batches without confirmation; use `--dry-run` to preview.

## Environment Variables

- `BATCH_CONCURRENCY`: max concurrency for batch operations (`push_*` / `switch_branch` / `sync_branch` / `sync_master`), default `4`. Example: `BATCH_CONCURRENCY=8 push_canary`.

## Requirements

- **Python 3.10+** (thin entrypoints and core logic)
- **Git** (merge_* / push_* / switch_branch / sync_master / fetch_all / delete_branch)
- **macOS** (`n` uses `say`, `unsleep` uses `caffeinate`)
- **rich** (output formatting, `pip install rich`)
- **pgrep / ps / lsof / kill** (kk / kkp)

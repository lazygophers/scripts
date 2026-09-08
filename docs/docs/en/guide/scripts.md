# Scripts

## No install: run straight from GitHub

```bash
uvx git+https://github.com/lazygophers/scripts                     # list every tool (same as lazyhelp)
uvx --from git+https://github.com/lazygophers/scripts checkwork    # run any single one
uv tool install git+https://github.com/lazygophers/scripts         # permanent install, commands stay on PATH
```

`uvx` ships with [uv](https://docs.astral.sh/uv/): it downloads a tool, runs it once and leaves nothing behind — no clone needed. With no command name it runs the `scripts` entry point, which is `lazyhelp`; with `--from`, uv requires an explicit command name.

## Install

```bash
./bin/inject            # generate ~/.scripts.sh and source it into all rc files
./bin/inject show     # preview what will be written
./bin/inject uninstall  # uninstall
```

inject is idempotent — re-running won't duplicate entries. Restart your shell or `source ~/.zshrc` afterwards to call commands from any directory.

## Feature Table

Seven categories by purpose. Terminal index: `lazyhelp`; full usage: `<tool> --help`; AI-facing guidance: `<tool> --skills`.

### Git Workflow

| Script | Function | Example |
| :--- | :--- | :--- |
| `merge_canary` | Merge current branch → canary, stay on canary | `merge_canary [--dry-run]` |
| `merge_develop` / `merge_dev` / `merge_test` | Same, targets develop / dev / test respectively | `merge_develop` |
| `merge_master` | Merge current branch → main branch (auto-detect master/main), stay on target | `merge_master` |
| `merge_branch` | Merge current branch → given branch (branch name is required first arg) | `merge_branch feature/x` |
| `push_canary` | Merge current branch → canary, push then switch back | `push_canary [--stay]` |
| `push_develop` / `push_dev` / `push_test` | Same, targets develop / dev / test respectively |  |
| `push_master` | Same, target is the auto-detected main branch |  |
| `push_branch` | Push current branch to given branch (branch name is required first arg) | `push_branch feature/x` |
| `switch_branch` | Batch switch branches (create from auto-detected main branch if missing) | `switch_branch <branch>` |
| `sync_branch` | Batch sync current (or given) branch to origin/<branch> | `sync_branch [branch] [--force]` |
| `sync_master` | Batch sync main branch (auto-detected) | `sync_master` |
| `delete_branch` | Delete local branch (single-repo; batch if not in git dir) | `delete_branch <name> [--force] [-y]` |
| `delete_branch_remote` | Delete remote branch (single-repo; batch if not in git dir) | `delete_branch_remote <name> [--remote <r>] [-y]` |

> Running the above in a non-git directory auto-batches: scans subdirectory Git repos and runs one by one.

### Git Collaboration

| Script | Function | Example |
| :--- | :--- | :--- |
| `commit` | Auto-commit changes (calls claude for message) | `commit` |
| `mr` | Auto-create PR/MR (calls claude for title/body, default draft) | `mr [base]` |
| `issue` | Auto-create Issue (calls claude for title/body) | `issue` |
| `squash_pr` | Squash source into single commit → feeds into mr | `squash_pr [source] <target>` |
| `fetch_all` | Batch fetch all Git repositories | `fetch_all` |
| `list_branch` | List local branches (single-repo or scan all repos, cross-repo dup names marked ⟱) | `list_branch` |

### Build & Check

| Script | Function | Example |
| :--- | :--- | :--- |
| `checkwork` | Pre-push multi-language build gate (Go/Rust/Python/Java/Node) + voice notification | `checkwork` |
| `check_ai` | AI API endpoint connectivity check (empty POST) | `check_ai` |
| `cicd` | Poll current branch CI/CD, print final result | `cicd` |

### Data & Network

| Script | Function | Example |
| :--- | :--- | :--- |
| `archery` | Archery SQL platform CLI (query / release workflow, per-domain login) | `archery query execute 'select 1' --instance-name prod --db-name orders` |
| `grafana` | Grafana HTTP API CLI (per-domain login) | `grafana health` |
| `ovpn` | OpenVPN client (auto-fills credentials & TOTP, split tunneling) | `ovpn connect` |
| `vpn-prio` | Adjust macOS network service priority (lower OpenVPN default route) | `vpn-prio --help` |
| `ipinfo` | Query LAN IP + network type (hotspot detection) | `ipinfo` |
| `disable-ipv6` / `enable-ipv6` | Disable/enable IPv6 on all network services (sudo required) | `sudo disable-ipv6` |

### Web Search

| Script | Function | Example |
| :--- | :--- | :--- |
| `websearch` | Multi-engine web search (all engines key-free & parallel, dedup by URL) | `websearch rust async` |
| `webgrab` | Fetch page to Markdown (anti-bot direct fetch + Playwright render + 34 site adapters + persistent login) | `webgrab https://example.com` |

### Process & Runtime

| Script | Function | Example |
| :--- | :--- | :--- |
| `kk` | Kill processes by name | `kk nginx` |
| `kkp` | Kill processes by port | `kkp 8080` |
| `loop` | Loop command execution, track success/failure | `loop 10 curl url` |
| `unsleep` | macOS caffeinate anti-idle | `unsleep timed 2h` |

### Files & System

| Script | Function | Example |
| :--- | :--- | :--- |
| `cpd` | Deep copy (default add/update only; `-f` deletes target extras) | `cpd src/* dest/` |
| `n` | macOS voice broadcast (`say`) | `n "build complete"` |
| `inject` | Inject bin/ into shell PATH | `inject` |
| `graphwatch` | graphify daemon: registered dirs auto-rebuild knowledge graph | `graphwatch add <dir>` |
| `lazyhelp` | Terminal tool index + `--help` forwarding | `lazyhelp help <tool>` |

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

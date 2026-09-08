# Introduction

`scripts` is a collection of dev-efficiency utilities — quick shortcuts for common dev and ops tasks. `bin/` holds only thin shells; the implementations live in `lib/cli/` and the shared helpers in `lib/`.

## Highlights

- **Thin entrypoints**: every script under `bin/` is the same shell (path fix + `from lib.cli.<module> import main`) — no business logic and no symlinks; the implementation is `lib/cli/<name>.py` and the shared helpers are flat modules under `lib/`.
- **Run remotely, no install**: every command is registered under `[project.scripts]`, so `uvx git+https://github.com/lazygophers/scripts` runs without cloning.
- **Task-based taxonomy**: Git Workflow / Git Collaboration / Build & Check / Data & Network / Web Search / Process & Runtime / Files & System — one page via `lazyhelp` in the terminal.
- **Batch operations**: `merge_*` / `push_*` / `switch_branch` / `sync_master` cover both single-repo and multi-repo batch.
- **Safety first**: process management self-exclusion, working-tree cleanliness checks and rollback before Git operations.

## Quick Start

Run once, install nothing:

```bash
uvx git+https://github.com/lazygophers/scripts                     # list every tool
uvx --from git+https://github.com/lazygophers/scripts checkwork    # run any single one
```

Keep them on this machine:

```bash
./bin/inject            # inject the cloned bin/ into shell PATH
```

Restart your shell afterwards, then call `checkwork` / `merge_canary` / ... from any directory.

See [Scripts](./scripts.md) and the GitHub repo [lazygophers/scripts](https://github.com/lazygophers/scripts).

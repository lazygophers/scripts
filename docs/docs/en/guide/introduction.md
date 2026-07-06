# Introduction

`scripts` is a collection of dev-efficiency utilities — quick shortcuts for common dev and ops tasks. Bash/Python thin entrypoints, with core logic settled in `lib/`.

## Highlights

- **Thin entrypoints**: scripts under `bin/` are just 3 lines of path hack + import; all business logic lives under `lib/commands/`.
- **Domain-classified**: `build` / `file` / `git` / `process` / `misc` / `system`, mutually isolated.
- **Batch operations**: `merge_*` / `push_*` / `switch_branch` / `sync_master` cover both single-repo and multi-repo batch.
- **Safety first**: process management self-exclusion, working-tree cleanliness checks and rollback before Git operations.

## Quick Start

```bash
./bin/inject            # inject bin/ into shell PATH
```

Restart your shell afterwards, then call `checkwork` / `merge_canary` / ... from any directory.

See [Scripts](./scripts.md) and the GitHub repo [lazygophers/scripts](https://github.com/lazygophers/scripts).

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a collection of script utilities designed to enhance development productivity. The scripts automate common development and operations tasks including version control, build checking, process management, and system notifications. Bash files act as entrypoints while the core logic lives in Python, designed primarily for macOS, with most functionality compatible with Linux systems.

## Repository Structure

`bin/*` are thin Bash wrappers (or symlinks) delegating to `lib/*.py`; enumerate with `ls bin/` and `ls lib/`. Conventions not obvious from a file listing:

- `merge_*` / `push_*` are symlinks to `bin/_gitwf`, dispatched by target name; `merge_master`/`push_master` auto-detect the remote default branch (master/main).
- `switch_branch` / `sync_master` resolve the real default branch per repo (`origin/HEAD` → `remote set-head --auto` → enumerate `origin/main`/`origin/master` → fall back to `master`) before creating/tracking; never hardcode `origin/master`.
- **Every `bin/*` entry must wrap its top-level call in `lib.ui.timed(fn, label="<name>")(...)`** so start/end/elapsed prints to stderr (dim) on exit. This is mandatory: new scripts add it, existing scripts keep it. Pattern: `raise SystemExit(timed(main, label="foo")(sys.argv))`.
- `reindex` is local-only (not in `bin/`, not tracked).

## Key Architecture Patterns

### Function Library Pattern

The Python modules under `lib/` provide:

- Core functions: `check_bit_clean()`, `check_build()`, `update_branch()`, `retry_command()`
- Smart build detection for Go projects with `app/` subdirectories
- Project-specific exclusions (e.g., `pay-core`, `*dao-*` projects)
- Git operations use standard `git` (avoid interactive wrappers in automation)
- Unified output via Rich `Reporter` (fallback to plain stderr)
- Error handling and notification integration
- Network retry mechanisms for unreliable connections

Scripts keep Bash wrappers thin and delegate logic to Python (`lib/*.py`).

### Git Workflow Architecture

The `merge_*` / `push_*` scripts (all symlinks to `bin/_gitwf`, dispatched by target name) implement a sophisticated Git workflow that:

- Uses standard `git` only (no interactive prompts)
- Checks working directory cleanliness before dangerous operations
- Default target branch depends on the invocation: `merge_canary` / `push_canary` target `canary`; `_canary`/`_develop`/`_test` map to those branches; `merge_master` / `push_master` auto-detect the remote default branch (master/main); other names target the given branch
- If the target branch does not exist on the remote, it is auto-created from `origin/HEAD` and pushed
- If the current branch has no remote ref yet, it is auto-pushed with `git push -u` before any pull
- Handles automated branch switching and merging to the target branch
- **Dry-run conflict preview before merge**: `git merge-tree --write-tree` (Git ≥2.38, zero side-effects) probes base↔head; on conflict the merge is aborted and rolled back without executing (old git falls back to `merge --no-commit` + abort)
- Includes comprehensive conflict detection and automatic rollback mechanisms
- Implements retry logic for network operations with exponential backoff
- Prevents execution directly on the target branch (safety mechanism)

### Process Management Pattern

Both `kk` and `kkp` scripts implement safe process termination with:

- Self-exclusion mechanisms to prevent killing the script itself or parent processes
- Comprehensive process discovery using `pgrep`, `ps`, and `lsof`
- Tabular output formatting for process information display
- Confirmation prompts and detailed process information before termination

### Error Handling and Notifications

All scripts follow a consistent error handling pattern:

- Immediate termination on critical errors with descriptive messages
- Integration with notification system using `n` for voice notifications
- Color-coded terminal output for different message types
- Automatic cleanup and rollback on failures

## Common Development Commands

Since this is a Shell script collection, there are no traditional build/lint/test commands. Instead:

Run scripts from the repository root (`./bin/<name>`; `chmod +x bin/*` once). Each has `--help`.

### Important Usage Notes

- **Dependencies**: Scripts with dependencies must be run from the repository root directory
- **Git Commands**: Scripts use standard `git`
- **Safety First**: `merge_*` / `push_*` cannot be run directly on the target branch (safety mechanism)
- **Voice Notifications**: Use `n` for system voice notifications. All `bin/*` (except `n`) accept `--no-say` (or `SCRIPTS_NO_SAY=1`) to mute; implemented via `lib/notify.py` (`consume_no_say` + `set_say_disabled`), so every bin entry must call `sys.argv = consume_no_say(sys.argv)` before its main/argparse.
 - **Output**: All Python scripts prefer Rich `Reporter` output (fallback to plain stderr)

### Testing Scripts

For `cpd`, a Python `unittest` suite exists:

```bash
python3 -m unittest discover -s tests -q
```

For other scripts without tests, validate by:

- Running in safe, isolated environments first
- Verifying expected outputs match documented behavior
- Testing error conditions and rollback mechanisms
- Checking voice notifications work properly

### Implementation Note

When shell behavior becomes complex (e.g., glob expansion, deep copy semantics, checksum comparisons), implement the core logic in Python and keep the Bash wrapper thin.

When users run `cpd src/* dest/` without quotes, the shell expands `src/*` into multiple arguments. `cpd` should treat the last argument as the destination and all preceding arguments as sources.

When handling copy semantics that imply “everything”, remember that shell `*` expansion does not include dotfiles; explicitly include `.xxx` entries when appropriate.

For `cpd`, print the copy plan (sources/dest) and per-entry execution status during the run; execution lines must be Chinese, must not include a `cpd:` prefix, and must only show paths relative to the destination base (e.g. `.claude-plugin/plugin.json`). Directories that already exist should be treated as skipped (do not repeatedly "create"), and symlinks must be kept in sync. Verify md5 after copying to ensure destination content matches the source (can be disabled via `CPD_VERIFY_MD5=0`).

## Platform Dependencies

### Required Tools

- **Shell**: Bash environment (v4.0+) required for all scripts
- **Process tools**: `pgrep`, `ps`, `kill`, `lsof` for process management scripts
- **File utilities**: Standard Unix tools (`find`, `grep`, `basename`, `dirname`)

### macOS Specific Features

- **Voice notifications**: The `n` script uses macOS `say` command for audio feedback
- Scripts are optimized for macOS but most functionality is compatible with Linux systems

## Safety Mechanisms

### Git Operations Safety

- Comprehensive working directory cleanliness checks before any dangerous operations
- Automatic rollback mechanisms on merge conflicts or failures
- Branch protection: prevents running `merge_*` / `push_*` directly on the target branch
- Network retry logic with exponential backoff for unreliable connections
- Original branch restoration on any failure

### Process Management Safety

- Self-exclusion mechanisms to prevent killing the executing script or parent processes
- Process validation and confirmation prompts before termination
- Detailed process information display before any termination action
- Protection against killing critical system processes

### Build and Integration Safety

- Multi-project type detection (Go projects with `go.mod`, Node.js projects with `package.json`)
- Selective compilation for complex project structures (handles `app/` subdirectories)
- Project-specific exclusions (e.g., `pay-core`, `*dao-*` projects)

## Script Dependencies and Architecture

### Function Library Implementation Details

The `lib/` modules provide these key functions:

#### `check_build()` - Multi-Language Build Check (CI/CD Pre-check)

CI/CD build 前置拦截器：防止 push 后 CI 连 build 都过不去。支持多语言混合项目，verbose 实时输出，零产物（check-only / build 到 `/dev/null`）。

- **Go**: `go build -v -o /dev/null`，编译 `cmd/`/`app/` 子目录 main 包 + 根目录，排除 `pay-core`/`*dao-*`
- **Rust**: `cargo check --verbose`（不生成二进制）
- **Python**: `py_compile` 语法编译（致命），`mypy`/`ruff` warn-only（仅警告不中止）
- **Java**: gradle `compileJava` / maven `mvn compile`（产物留项目自管 build/target）
- **C/C++**: cmake configure only（只 `-B` 配置不 build，不产二进制）；Makefile 不支持 check（规则任意、产物不可控），直接跳过
- **Node.js**: 按 lockfile 优先级（bun > yarn > pnpm > npm）选包管理器；`build` script 经白名单识别（仅 tsc/nuxt build/rspack build 等纯编译命令）才跑，含 watch/serve/dev 或无法识别则跳过 + warn（`CHECKWORK_NODE_BUILD=1` 强制执行）；`typecheck` script warn-only；兜底 `tsc --noEmit`

环境变量：`CHECKWORK_PARALLEL=1` 开启多语言检查点并行（默认串行）。

### Execution Requirements

- Scripts with dependencies must be executed from the repository root directory
- Python implementation files must be in the same directory as their Bash wrappers
- All scripts use relative path references for local dependencies

### Integration Points

- Success/failure states are consistently reported with both visual and audio feedback
- Color-coded output provides immediate visual feedback for different operation states
- Voice notifications provide audio feedback for important events

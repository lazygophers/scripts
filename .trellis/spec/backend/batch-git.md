- 强制规则起首，禁 h1，禁描述性空话（本文件为可机器验证的命令式契约）
- 范围：`lib/batch_git.py` 及其薄壳入口 `bin/sync_master` / `bin/switch_branch` / `bin/push_*` / `bin/delete_branch` / `bin/delete_branch_remote` / `bin/fetch_all`，凡批量 Git 仓库操作均受本契约约束
- 引用版本：本文档引用的 file:line 以 `master@f654d79` 为准；行号漂移时按函数名定位

## 仓库扫描（scan_repos）

- 严禁按 remote 提供商过滤。旧名 `scan_gitlab_repos` 已退化为 `scan_repos` 的别名（`lib/batch_git.py:57`），仅保留向后兼容，禁新增任何"仅 GitLab / 仅 GitHub"的判定逻辑。验收：`tests/test_batch_git.py::TestScanRepos::test_includes_github_and_bare` 必须通过
- MUST 同时识别 `.git` 作为目录（普通仓库）和 `.git` 作为文件（submodule / git worktree 的 gitdir 指针）。`lib/batch_git.py:47` 用 `".git" in dirnames or ".git" in filenames` 双判定，禁退回仅查 `dirnames`。验收：`tests/test_batch_git.py::TestScanRepos::test_finds_submodule_git_file` 必须通过
- MUST 在命中 `.git` 目录后 `dirnames.remove(".git")`（`lib/batch_git.py:49-51`），禁 `os.walk` 误入 `.git` 内部
- 测试规约：`scan_repos` 的单测 MUST 用 `unittest.mock.patch("lib.batch_git.os.walk")` 注入 `(dirpath, dirnames, filenames)` 三元组；禁在 mock 场景调用 `Path.exists()`（mock 不实现文件系统，会破坏断言）。参见 `tests/test_batch_git.py:47-90`

## 删除类批量操作（delete_branch / delete_branch_remote）

- 删除类操作 MUST 默认 `confirm=True`（`lib/batch_git.py:560-568`、`599-607` 的 `run_batch(..., confirm=True)`）。`-y` / `BATCH_NO_CONFIRM=1` 是唯一显式跳过通道（`bin/delete_branch`、`bin/delete_branch_remote` 在 `args.yes` 时 set `BATCH_NO_CONFIRM=1`）
- MUST 安全 skip，禁对以下场景执行删除：
  - 本地当前分支 == target → skip（`lib/batch_git.py:536-538`，禁删自己）
  - 本地无 `refs/heads/<target>` → skip（`lib/batch_git.py:540-545`）
  - 未合并且未传 `--force` → skip 并提示 `--force`（`lib/batch_git.py:547-553`，用 `-d` 而非 `-D`）
  - 远端无 `refs/remotes/<remote>/<target>` → skip（`lib/batch_git.py:578-583`）
- 删远端成功后 MUST 立即 `git fetch --prune <remote>` 清本地 tracking ref（`lib/batch_git.py:591-593`），禁遗留 stale remote-tracking 引用
- 单仓入口（当前 cwd 在 git 仓库内）走 `bin/delete_branch::_delete_local_single` / `bin/delete_branch_remote::_delete_remote_single`，与批量入口共享同一组 skip / prune 规则；两路径行为 MUST 一致，禁单仓路径绕过上述安全 skip

## 命名一致性（批量脚本组）

- 批量脚本组 MUST 用「动词_对象」命名，禁 `git_` 前缀。现行规范名（`bin/` 下）：`sync_master` / `sync_branch` / `switch_branch` / `push_auto` / `push_canary` / `push_develop` / `push_test` / `delete_branch` / `delete_branch_remote` / `fetch_all`
- 旧名 `git_fetch_all` 已重命名为 `fetch_all`，禁回退。新增批量脚本 MUST 遵同一风格
- `scan_gitlab_repos` 是历史名，仅作 alias 保留（`lib/batch_git.py:56-57`），新代码 MUST 直接调 `scan_repos`

## 批量执行流程（run_batch）

- 单仓库操作 MUST 返回 `(status, detail)`，`status ∈ {"ok", "skip", "fail"}`（`lib/batch_git.py:106` 的 `OperationFn` 契约）。禁用 bool / 异常表达 skip / fail（异常由 `_run_one` 兜底转 `fail`，`lib/batch_git.py:163-165`）
- 单仓操作 MUST 用 per-repo buffer（`io.StringIO` + `Reporter.from_buffer`，`lib/batch_git.py:158-159`），完成即整段 flush（`lib/batch_git.py:176-178`），禁多线程直接写共享 stderr 导致 Rich 输出交错
- MUST 用 `ThreadPoolExecutor` 并行（`lib/batch_git.py:168`），并发度读 `BATCH_CONCURRENCY` 环境变量（默认 4，`lib/batch_git.py:139`）。`KeyboardInterrupt` MUST `pool.shutdown(wait=False, cancel_futures=True)`（`lib/batch_git.py:189-191`）
- 闭包捕获 per-batch 参数（`_delete_branch_one_factory` / `_push_one_factory` 等 factory 模式），禁退回模块级 `_TARGET` / `_FORCE` / `_DRY_RUN` / `_EXTRA` 全局态（`lib/batch_git.py:198-199` 注释为反模式声明）

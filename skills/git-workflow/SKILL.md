---
name: git-workflow
description: 使用本仓库的 merge_* / push_* / switch_branch / sync_branch / delete_branch / list_branch / fetch_all 脚本做分支合并、推送与批量分支操作。当任务涉及合并当前分支到 canary/develop/test/master、批量推送/切换/删除分支时使用，代替手写 git 命令。
---

# git-workflow

`bin/` 下的 git 工作流脚本（须已 `./bin/inject` 注入 PATH，或直接 `./bin/<name>` 运行）。

## 合并与推送

| 命令 | 行为 |
|------|------|
| `merge_<target> [--dry-run]` | 合并当前分支到 target，**留在** target。canary/develop/test 固定目标；`merge_master` 自动识别远端主分支（master/main） |
| `push_<target> [--stay]` | 合并到 target、推送后**切回**原分支；`--stay` 留在 target |
| `push_branch` | 批量推送当前分支到远端 |

要点：

- **不能在目标分支上执行**（安全机制，脚本自身拦截）。
- push_* 合并前先跑 `git merge-tree` 干跑冲突预检（Git ≥2.38），有冲突直接中止并回滚，不产生半合并状态。
- push_* 默认带 checkwork 构建闸门，`--no-check` 跳过。
- 在**非 git 目录**执行 `push_*` 时自动批量模式：扫描子目录所有 git 仓库逐个推送，`--dry-run` 预览。
- 远端分支不存在时自动从 `origin/HEAD` 创建；当前分支无远端时自动 `push -u`。
- 网络操作自带指数退避重试。

## 分支管理

```bash
switch_branch <branch>      # 批量切换（不存在则从主分支创建）；主分支自动识别
sync_branch [branch] [--force]  # 批量同步到 origin/<branch>
sync_master                 # = sync_branch master
delete_branch <name> [--force] [-y]     # 删本地分支；非 git 目录时批量
delete_branch_remote <name> [--remote <r>] [-y]  # 删远端分支
list_branch                 # 列出本地分支（单仓或扫描所有仓库，跨仓同名标 ⟱）
fetch_all                   # 批量 fetch 所有 git 仓库
```

批量操作并发上限：`BATCH_CONCURRENCY=8 push_canary`（默认 4）。

## 通用选项

所有 `bin/*` 支持 `--no-say`（静音 macOS 语音）与 `--debug`（打印成功命令输出）。

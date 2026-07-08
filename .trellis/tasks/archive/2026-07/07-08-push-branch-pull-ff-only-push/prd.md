# PRD: push_branch — 批量推送当前分支到远端

## 背景

现有 `bin/sync_branch` 方向是 远端 → 本地（`reset --hard origin/<branch>`）。
缺反向操作：本地 → 远端。需求是"先同步远端到本地，再推本地到远端"，
即对每个仓库先 `pull --ff-only` 拉远端，再 `push` 推本地。

## 目标

新增 `bin/push_branch` 薄壳 + `lib/batch_git.push_branch_all`，批量推送当前分支（或指定分支）到远端同名分支。

## 需求

- 入口：`push_branch [branch] [--force]`
  - `branch` 省略 → 推送各仓库当前分支（与 `sync_branch` 对称）
  - `--force` → `push --force-with-lease`（默认普通 push）
- 单仓库流程（`_push_branch_one_factory`）：
  1. `git fetch --prune -q origin`，失败 → fail
  2. 取目标分支：省略=当前分支（`git branch --show-current`，detached → skip）；指定=该分支
  3. 无本地 `<branch>` → skip；无 `origin/<branch>` → 视为新分支（允许 push 创建）
  4. 工作区 dirty → skip（`git diff-index --quiet HEAD --`）
  5. `git pull --ff-only origin <branch>`
     - 失败/分叉/冲突 → **skip 该仓库继续**（不中断批量）
  6. `git push [-u] [--force-with-lease] origin <branch>`
     - 远端不存在该分支 → 加 `-u`（建立 tracking）
     - `--force` → `--force-with-lease`
  7. 成功 → ok，detail 报推送的 commit 区间或"新分支/无变化"
- 复用 `run_batch`（并行、buffer、汇总、通知），`confirm=False`
- 与 `sync_branch` 结构对称：factory 返回 `(status, detail)`

## 非目标

- 不做 merge / rebase（ff-only，分叉即 skip）
- 不切分支（push 当前分支，不像 `push_canary` 那样 merge 到 target）
- 不还原原 checkout

## 验证

- `push_branch --help` 显示用法
- 单 repo 测试：无变化/有新 commit/远端无分支/dirty/分叉 各路径
- 批量扫描现有仓库目录不报错

# PRD: delete_branch + delete_branch_remote

## 背景

批量操作组（sync_branch/switch_branch/push_*）缺「删分支」。用户需删本地指定分支、远端指定分支。

## 目标

新增两个独立删分支脚本，双模式（单仓默认 / 非 git 目录自动批量），删前交互确认。

## 范围

### 新增文件

| 文件 | 功能 |
|---|---|
| `bin/delete_branch` | 删**本地**分支 `git branch -d/-D <name>` |
| `bin/delete_branch_remote` | 删**远端**分支 `git push origin --delete <name>` |
| `lib/branch_delete.py`（或并入 batch_git） | 删除逻辑 + 双模式判定 |

### 改动文件

- `lib/batch_git.py`：新增 `delete_branch_all` / `delete_branch_remote_all`（批量）
- `README.md` + 多语言 + docs + CLAUDE.md：脚本表新增两行

## 设计决策

### D1: 双模式（仿 push_*）

- 当前目录是 git 仓库（有 `.git`）→ 单仓操作
- 非 git 目录 → 批量扫描所有 GitLab 仓库
- `--help` 任何环境转发 argparse

### D2: 两个独立脚本

- `delete_branch <name>` — 仅本地
- `delete_branch_remote <name>` — 仅远端
- 职责单一，与 sync_branch(同步) / switch_branch(切换) 风格一致

### D3: 交互确认（用户选定）

- 批量模式：逐仓库或汇总后一次 y/n 确认
- 单仓模式：显示将删的分支 + y/n
- `--force` / `-y` 跳过确认（非交互场景）
- 复用 run_batch 的 confirm 机制（switch/sync 用 confirm=False，删分支用 confirm=True）

### D4: 删除语义

**本地**：
- 默认 `git branch -d`（安全，仅删已合并）
- `--force` 用 `git branch -D`（强删未合并）
- 当前分支 = 目标分支 → skip（不能删自己），提示先 switch

**远端**：
- `git push <remote> --delete <name>`
- 默认 remote=origin，`--remote <name>` 可指定
- 删后 `git fetch --prune` 清理本地 tracking ref

### D5: 批量场景的「目标分支」

- 位置参数 `<branch>`：所有仓库统一删该分支名
- 无该分支的仓库 → skip（不报错）

## 验收标准

- [ ] `bin/delete_branch <name>` 单仓删本地分支，删前 y/n 确认
- [ ] `bin/delete_branch <name> --force` 强删未合并分支
- [ ] `bin/delete_branch` 在当前分支 = 目标时 skip + 提示
- [ ] `bin/delete_branch_remote <name>` 单仓删 origin/<name>
- [ ] `bin/delete_branch_remote <name> --remote upstream` 删指定 remote
- [ ] 非 git 目录执行 → 批量扫描所有 GitLab 仓库
- [ ] 批量模式删前汇总确认
- [ ] `-y` / `--yes` 跳过确认
- [ ] 无该分支的仓库 skip 不报错
- [ ] 测试覆盖核心逻辑（单仓 + 工厂函数）
- [ ] README/docs/CLAUDE.md 更新

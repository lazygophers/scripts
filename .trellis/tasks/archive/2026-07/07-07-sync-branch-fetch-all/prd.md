# PRD: sync_branch + fetch_all 命名统一

## 背景

`bin/` 下批量 Git 操作脚本命名不一致：
- `sync_master` / `switch_branch` / `push_canary` — 动词_对象，无前缀
- `git_fetch_all` — `git_` 前缀 + `_all` 后缀，孤儿命名

且缺少「同步当前分支 / 指定分支」的批量脚本（`sync_master` 硬编码 master）。

## 目标

1. **命名统一**：`git_fetch_all` → `fetch_all`，与批量组一致。
2. **新增 `sync_branch`**：默认同步当前目录下所有 Git 仓库的**当前分支**到 `origin/<当前分支>`；接受位置参数 `<branch>` 同步**指定分支**（不切换 checkout）。

## 范围

### 改动文件

| 文件 | 改动 |
|---|---|
| `bin/git_fetch_all` → `bin/fetch_all` | 重命名（git mv） |
| `bin/sync_branch` | 新增（薄壳，argparse 位置参数 + `--force`） |
| `lib/batch_git.py` | 新增 `_sync_branch_one_factory` + `sync_branch_all(branch=None, *, force=False)`；保留 `sync_master_all`（重构为调用通用版，branch="master"） |
| `lib/git.py` | `fetch_all` 函数名不变（bin 改名即可，lib 函数无 `git_` 前缀） |
| `tests/test_find_git_repos.py` 等 | 若有 fetch_all 引用则更新（待 grep 确认） |
| `README.md` + 多语言 README | 脚本表更新（`git_fetch_all`→`fetch_all`，新增 `sync_branch` 行） |
| `CLAUDE.md` | 脚本列表 + chmod 行 + 用法行更新 |

### 非目标

- 不改 `switch_branch` / `push_*` 行为。
- 不删除 `sync_master`（保留，作为 `sync_branch master` 的快捷别名）。

## 设计决策

### D1: 命名统一方向 = `fetch_all`（去 git_ 前缀）

理由：批量组（sync_master/switch_branch/push_canary）已形成「动词」风格，改一个比改全组 diff 小。lib 层函数本就无前缀（`fetch_all`/`sync_master_all`），bin 改名后与 lib 更对称。

### D2: `sync_branch <branch>` 语义 = 只同步不切换

理由：与 `sync_master` 对称——`sync_master` 不要求调用者当前在 master 上，它在库内 checkout master → reset → 不还原原分支。`sync_branch <branch>` 同理：操作目标分支，不切回原分支也不是它的职责（要切换用 `switch_branch`）。

### D3: 对齐方式 = 硬对齐（reset --hard），与 sync_master 完全一致

- 默认：本地领先 → skip（保护未推送 commit）
- `--force`：丢弃本地领先，硬 reset
- 工作区脏 → skip
- 无 `origin/<branch>` → skip

复用 `_sync_one_factory` 逻辑，参数化分支名。

### D4: `sync_branch` 无入参 = 同步各仓库自己的当前分支

每个仓库独立取 `git branch --show-current`，对齐到 `origin/<该分支>`。不同仓库可在不同分支。

## 验收标准

- [ ] `bin/fetch_all` 存在且可执行，`bin/git_fetch_all` 不存在
- [ ] `bin/sync_branch` 存在且可执行
- [ ] `sync_branch`（无参）同步各仓库当前分支
- [ ] `sync_branch <branch>` 同步指定分支，不改变调用者 checkout 状态
- [ ] `sync_branch --force` 丢弃本地领先 commit
- [ ] `sync_master` 行为不变（回归通过）
- [ ] `python3 -m unittest discover -s tests -q` 通过
- [ ] README / CLAUDE.md 更新

# squash_pr — squash PR 脚本

## 背景

现有 `prc` 脚本（`bin/prc` → `lib/prc_wf.py`）能自动创建 PR/MR，但直接对 feature 分支开 PR 会带出多条 commit。需要一个前置脚本：以 source 分支为基准创建 `<source>_pr` 分支，把 source 自分叉以来的全部改动压成单 commit，push 后交给 `prc <target>` 开 PR。

## 目标

提供 `squash_pr [source] <target>` 命令：source = 被压缩的特性分支（**选填，默认当前分支**），target = PR 的 base（**必填**）。执行后产出一个仅含单 commit 的 `<source>_pr` 分支并对接 `prc` 开 PR，源分支与 target 分支本身不动。

## 非目标

- 不实现开 PR 逻辑（复用 `prc`）
- 不改写 source 分支历史
- 不自动动本地 target 分支（本地无 target 则不创建；比较/reset 基准一律用 `origin/<target>`）

## 用户流程

```
squash_pr <target>            # source 默认当前分支
squash_pr <source> <target>   # 显式指定 source
```

参数解析：仅 1 个位置参数 → 视为 target，source = 当前分支；2 个位置参数 → source, target。target 缺失 → 报错退出。

## 功能需求

### FR1 — 前置护栏
- 参数解析：source 选填，缺省取 `get_current_branch()`；target 必填，缺失即报错退出
- 复用 `lib.git.check_bit_clean()`：工作区非空即中止
- 记录起始分支（`lib.git.get_current_branch()`），失败时回滚用

### FR2 — fetch 与校验
- `git fetch origin <source> <target>`（用 `lib.exec.retry_command`，max_retries=3）
- source 落后 `origin/<source>` → 中止并提示先推送/拉取 source
- target 仅更新远端引用；**本地存在 target 分支**才把它 checkout + reset 到 `origin/<target>`，本地无则跳过

### FR3 — 合并冲突预演 #1（reset 前）
- 检测 source 与 `origin/<target>` 的三方合并是否会冲突
- 用 `git merge-tree --write-tree --name-only <source> origin/<target>`（git ≥ 2.38）判冲突；不可用则回退临时 `merge --no-commit --no-ff` 后 abort
- 有冲突 → 中止回滚，列出冲突文件

### FR4 — 建 PR 分支
- PR 分支名 = `<source>_pr`
- 本地或远端（`lib.git.remote_branch_exists`）已存在 `<source>_pr` → **AskUserQuestion 询问是否删除**（删除则本地 `branch -D` + 远端 `push origin --delete`；不删则中止）
- `git checkout <source>` → `git checkout -b <source>_pr`

### FR5 — squash
- `git reset --soft $(git merge-base <source> <target>)`
- 合并冲突预演 #2（见 FR7）放在 commit 之后、push 之前

### FR6 — commit message 聚合
- `git log --no-merges --format=%s <merge-base>..<source>`
- **只过滤 git 自动生成的**：`--no-merges` 已跳 merge commit；额外正则过滤 `^Merge (branch|pull request|tag|remote-tracking)`
- 不外加项目前缀过滤（用户已确认）
- 去重保序拼成 subject；body 列出原 subject bullet
- **聚合后为空** → 兜底 message = `squash: <source> → <target>`
- 单 commit

### FR7 — 合并冲突预演 #2（push 前）
- 同 FR3 机制，检测 `<source>_pr` vs `origin/<target>`
- 有冲突 → 回滚（回起始分支 + 删 `<source>_pr`）+ `n` 报错

### FR8 — push
- `git push -u origin <source>_pr>`（FR4 已确保无旧分支，普通 push 即可，禁 force）

### FR9 — 对接 prc
- push 后**停留在 `<source>_pr>` 分支**（`prc` 用 `current_branch()` 作 PR head）
- subprocess 调 `bin/prc <target>`（解耦 + 复用 `prc` 的 reporter 输出）
- `prc` 自己 fetch + 从 `origin/<target>..HEAD` diff 生成 PR title/body 并创建

### FR10 — 失败回滚
- squash 流程内部失败（fetch/校验/reset/commit/push 等）：`git checkout <起始分支>` + `git branch -D <source>_pr`（若已建）+ 删远端 `<source>_pr`（若已 push）+ 调 `lib.notify` / `n` 报错
- **prc 调用失败例外**：已 push 的 `<source>_pr` 分支**不回滚**（分支可能已挂 PR，删了丢工作；prc 失败多非致命，用户重跑 `prc <target>` 即可）— 仅报错退出 + 提示手动重跑 prc

## 技术约束

- 薄壳：`bin/squash_pr`（argparse 壳）→ `lib/squash_pr_wf.py`（逻辑），对齐 `prc` 模式
- 复用：`lib.git`、`lib.exec.run`/`retry_command`、`lib.ui.reporter`、`lib.notify`
- 仅用标准 `git`，禁交互式包装（项目硬规）
- Python 3，类型注解，ruff 清洁

## 验收标准

- `squash_pr <source> <target>` 在干净工作区 + source 已推 + 无冲突时，产出：单 commit 的 `<source>_pr` 远端分支 + 一条 PR（base=target, head=`<source>_pr`）
- PR diff 与 `git diff merge-base..source` 内容一致（仅 1 commit）
- source 分支、本地 target 分支（若有）历史不被改写
- 工作区脏 / source 落后 / 冲突 / 分支已存在 四种异常路径均正确中止回滚

## 测试要求（强制）

### T1 — 临时仓库端到端测试
在 `/tmp` 创建临时本地 git 仓库（含 bare 远端模拟 `origin`），跑完整流程：
- 构造 source（多条 commit，含 merge commit + 噪声）+ target 分叉场景
- 执行 `squash_pr <source> <target>`，验证：
  - `<source>_pr` 远端分支存在且仅 1 commit
  - 该 commit diff == `git diff merge-base..source`
  - source / target 历史未被改写
- 异常路径各跑一遍：工作区脏、source 落后远端、merge 冲突、`<source>_pr` 已存在
- 验证全量回滚（起始分支恢复、半成品分支删除）
- 跳过真实 prc 的 AI 调用（mock 或 `--dry-run` / `--no-prc` 开关隔离）

### T2 — 单元测试
`tests/test_squash_pr*.py`，对齐 `cpd` 的 `python3 -m unittest discover -s tests -q` 模式，覆盖：
- commit message 聚合逻辑（去重、merge 噪声过滤、空兜底）— 纯函数，易测
- 分支名生成、merge-base 计算、冲突检测返回值解析
- 用 tmp 仓库 fixture 隔离 git 状态

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| target 远端引用过期 | 每次先 fetch |
| `<source>_pr` 已存在 | 询问删除 |
| 聚合 message 为空 | 兜底固定文案 |
| push 后 prc 前 target 又被推新 | prc 自己 fetch + 预演 #2 双保险 |
| 失败残留半成品分支 | 全量回滚删本地+远端 |

## 开关

- `--dry-run`：打印计划（reset 目标、聚合的 message、将执行的命令）不实际执行
- `--no-prc`（测试用）：做完 push 停手，不调 prc 开 PR

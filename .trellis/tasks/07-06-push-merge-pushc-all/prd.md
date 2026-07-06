# PRD：重命名 push/merge 入口 + 合并 pushc_all

## 背景

现有 `bin/` 下 push/merge 入口为缩写命名（`pushc`/`pushdev`/`pushm`/`pusht`、`mergec`/`mergedev`/`mergem`/`merget`），均为 `_gitwf` 的 symlink，靠 argv[0] 分派 action+target。`pushc_all` 是独立脚本，调 `lib/batch_git.pushc_all`，扫描 GitLab 仓库批量执行 pushc（仅支持 canary，硬编码 `_pushc_one_factory`）。

命名不直观，且 `pushc_all` 与 `pushc` 割裂、仅 canary 可批量。

## 目标

1. **重命名**：所有 push/merge 入口改为 `动作_目标` 全称风格（下划线分隔）。
2. **合并**：移除 `pushc_all`，`push_canary` 等所有 push 入口自动识别单仓/批量场景。
3. **旧名清理**：删除旧 symlink，不保留别名。

## 需求规格

### 1. 新命名映射

| 旧名 | 新名 |
| --- | --- |
| `pushc` | `push_canary` |
| `pushdev` | `push_develop` |
| `pushm` | `push_auto` |
| `pusht` | `push_test` |
| `mergec` | `merge_canary` |
| `mergedev` | `merge_develop` |
| `mergem` | `merge_auto` |
| `merget` | `merge_test` |

### 2. merge 系列

- 仅单仓，不引入批量（现状无 merge_all）。

### 3. push 系列 — 自动识别批量

- **判据**：当前目录是否 git 仓库。
  - 是 git 仓库（有 `.git`）→ 单仓模式，走 `push_to(target)`。
  - 非 git 仓库 → 批量模式，扫描子目录 GitLab 仓库，逐个执行 push 到 target。
- 所有 push_* 入口都支持批量，不止 canary。
- 批量模式复用 `lib/batch_git.run_batch` + 泛化 `_pushc_one_factory` 为接受 target 参数。
- **批量模式无需确认，自动执行**（`confirm=False`），沿用原 `pushc_all` 行为。

### 4. pushc_all 处理

- 删除 `bin/pushc_all` 脚本。
- 逻辑并入 `push_canary`（及其他 push_* 入口的批量路径）。
- `lib/batch_git.pushc_all` 泛化为 `push_all(target, ...)` 或在 `_gitwf` 分派时直接调 `run_batch` + 泛化 factory。
- **`--dry-run` 统一为预览语义**：单仓列执行步骤不执行；批量列出待操作仓库不执行。合并原 `pushc_all` 的条件检查语义到批量 dry-run 输出。

## 改动范围

- `bin/_gitwf`：`_NAME_MAP` key 改新名；批量分派逻辑（自动识别 → push_to 或 run_batch）。
- `bin/` symlink：删旧名（pushc/pushdev/pushm/pusht/mergec/mergedev/mergem/merget），建新名 symlink → `_gitwf`。
- `bin/pushc_all`：删除。
- `lib/batch_git.py`：`pushc_all` 泛化为支持任意 target。
- `README.md`：更新入口名与用法。
- `CLAUDE.md`：若有引用，同步更新（待查）。
- 测试：新增/更新批量自动识别 + 新命名分派测试。

## 待确认

- [x] **自动识别判据**：当前目录是否 git 仓库（已确认）。
- [x] **批量确认门**：无确认，自动执行 confirm=False（用户实测反馈，推翻原推测）。
- [x] **`--dry-run` 统一**：预览语义，单仓列步骤/批量列仓库，均不执行（推测，用户离场，按统一性原则定）。
- [ ] CLAUDE.md 是否有 pushc/pushc_all 引用需同步（待实现时 grep）。

## 验收标准

- 新名 symlink 全部可用：`push_canary`/`push_develop`/`push_auto`/`push_test`/`merge_canary`/`merge_develop`/`merge_auto`/`merge_test`。
- 旧名 symlink 与 `pushc_all` 脚本全部删除。
- 在 git 仓库内执行 push_* → 单仓行为；在非 git 目录执行 → 批量扫描行为。
- 所有 push_* 入口都支持批量（不止 canary）。
- 测试通过：`python3 -m unittest discover -s tests -q`。
- README 无失效引用。

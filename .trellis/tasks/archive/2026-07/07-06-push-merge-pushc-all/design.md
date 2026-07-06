# Design：重命名 push/merge 入口 + 合并 pushc_all

## 关键约束（来自 codebase）

- `bin/_gitwf` 是 symlink 入口，靠 `argv[0]` basename 分派 `action+target`。
- `run_workflow`（lib/git_workflow.py:85）已支持 `--dry-run`（单仓预览步骤）、`--stay`。
- `lib/batch_git.run_batch(title, root, operation, *, confirm)` 是批量执行框架，`confirm` 参数可控确认门。
- `_pushc_one_factory(dry_run, extra)` 硬编码 canary（batch_git.py:178），需泛化 target。
- `pushc_all` 用 `confirm=False`（batch_git.py:262）—— 本次改为默认 confirm=True。

## 设计决策

### D1. 自动识别判据

`bin/_gitwf` 入口处判断 `Path.cwd() / ".git"` 是否存在：
- 存在 → 单仓：`push_to(target, argv)`（merge 系列同理 `merge_to`）。
- 不存在 → 仅 push 系列进批量：调泛化后的 `push_all(target, argv)`。

merge 系列在非 git 目录 → 报错退出（不引入批量，prd 已定）。

### D2. `_NAME_MAP` 改 key

```python
_NAME_MAP = {
    "merge_canary": ("merge", "canary"),
    "merge_develop": ("merge", "develop"),
    "merge_auto": ("merge", "auto"),
    "merge_test": ("merge", "test"),
    "push_canary": ("push", "canary"),
    "push_develop": ("push", "develop"),
    "push_auto": ("push", "auto"),
    "push_test": ("push", "test"),
}
```

### D3. `_gitwf` 分派逻辑

```python
action, target = _NAME_MAP[name]
in_git_repo = (Path.cwd() / ".git").exists()

if action == "merge":
    if not in_git_repo:
        stderr("merge_* 需在 git 仓库内执行")
        return 2
    return merge_to(target, sys.argv)

# push 系列
if in_git_repo:
    return push_to(target, sys.argv)
# 非 git 目录 → 批量
from lib.batch_git import push_all
return push_all(target, sys.argv)
```

### D4. `push_all(target, argv)` 泛化

`lib/batch_git.py`：
- `pushc_all` → `push_all(target, *, dry_run, yes, extra)`。
- `_pushc_one_factory(dry_run, extra)` → `_push_one_factory(target, dry_run, extra)`：把硬编码 `canary`/`origin/canary` 改为 `target`/`origin/{target}`，单仓执行调 `push_{target}`（即新名 symlink）而非硬编码 `pushc`。
- `run_batch(..., confirm=False)`：批量无确认，自动执行（用户实测反馈，沿用原 pushc_all 行为）。
- argparse 仅解析 `--dry-run`，移除 `--yes`/`-y`，余下透传 `extra`。

### D5. symlink 重建

删除旧 8 个 symlink（pushc/pushdev/pushm/pusht/mergec/mergedev/mergem/merget）+ `bin/pushc_all`，建新 8 个 → `_gitwf`。

### D6. `--dry-run` 统一

- 单仓：`run_workflow` 现有 dry-run（列步骤）不动。
- 批量：`run_batch` 前若 dry_run，先列待操作仓库（复用 `scan_gitlab_repos` + `print_repo_list`），每仓 factory 内 dry_run 不执行 push，仅报条件是否满足。两路径都不执行实际 push。

## 风险

- **R1**：旧名 symlink 删除后，外部脚本/alias/habit 调 `pushc` 失效。prd 已定不保留别名，需 README 标注迁移。
- **R2**：批量模式 factory 内调 `push_{target}`（新 symlink）依赖 PATH 中 bin/ 可执行 —— 若用户从其他目录调，`run` cwd=repo 但可执行名需在 PATH。原 `pushc_all` 同样调 `pushc`，沿用此假设。

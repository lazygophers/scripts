# squash_pr

压当前分支自分叉以来的改动为单 commit → 开 PR

## 用法

用法：`squash_pr COMMAND | <flags> [ARGS]...`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `run` | 执行 squash → push → 开 PR |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `ARGS` | 'str' |  |
| `--pr_branch=PR_BRANCH` | Optional['str \| None'] | None |
| `-d, --dry_run=DRY_RUN` | 'bool' | False |
| `--push_only=PUSH_ONLY` | 'bool' | False |

## 示例

```bash
squash_pr <target> [pr_branch]
```

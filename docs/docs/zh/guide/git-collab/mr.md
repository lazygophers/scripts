# mr

自动创建 PR/MR（调 claude 生成 title/body，默认 draft）

## 用法

用法：`mr COMMAND | <flags> [ARGS]...`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `create` | 创建 PR/MR |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `ARGS` | 'str' |  |
| `-d, --dry_run=DRY_RUN` | 'bool' | False |
| `-p, --publish=PUBLISH` | 'bool' | False |
| `-r, --reviews=REVIEWS` | Optional['str \| None'] | None |
| `-l, --labels=LABELS` | Optional['str \| None'] | None |
| `-a, --assignee=ASSIGNEE` | Optional['str \| None'] | None |
| `-s, --settings=SETTINGS` | Optional['str \| None'] | None |

## 示例

```bash
mr [base]
```

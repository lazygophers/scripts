# issue

自动创建 Issue（调 claude 生成 title/body）

## 用法

用法：`issue COMMAND | <flags> [ARGS]...`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `create` | 创建 Issue |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `ARGS` | 'str' |  |
| `-d, --dry_run=DRY_RUN` | 'bool' | False |
| `-l, --labels=LABELS` | Optional['str \| None'] | None |
| `-a, --assignee=ASSIGNEE` | Optional['str \| None'] | None |
| `-m, --milestone=MILESTONE` | Optional['str \| None'] | None |
| `-s, --settings=SETTINGS` | Optional['str \| None'] | None |

## 示例

```bash
issue
```

# kk

按进程名终止进程（正则）

## 用法

用法：`kk COMMAND | <flags> [PATTERNS]...`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `by_name` | 终止匹配的进程 |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `PATTERNS` | 'str' |  |
| `-d, --dry_run=DRY_RUN` | 'bool' | False |

## 示例

```bash
kk nginx
```

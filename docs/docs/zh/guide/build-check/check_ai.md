# check_ai

AI API 端点连通性检测（空 POST）

## 用法

用法：`check_ai COMMAND | <flags> [ARGS]...`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `probe` | 探测 AI 端点连通性 |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `ARGS` | 'str' |  |
| `-c, --count=COUNT` | 'int' | 5 |
| `--infinite=INFINITE` | 'bool' | False |
| `-t, --timeout=TIMEOUT` | 'float' | 15.0 |
| `--interval=INTERVAL` | 'float' | 5.0 |
| `-p, --proxy=PROXY` | Optional['str \| None'] | None |

## 示例

```bash
check_ai
```

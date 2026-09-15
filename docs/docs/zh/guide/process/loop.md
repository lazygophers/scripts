# loop

循环执行命令并追踪结果（成功即停或指定次数）

## 用法

用法：`loop COMMAND | <flags> [ARGS]...`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `force` | 强制跑满次数（失败也继续） |
| `infinite` | 无限循环 |
| `run` | 循环执行（成功即停；首 token 为数字时视为 count） |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `ARGS` | 'str' |  |
| `-f, --force=FORCE` | 'bool' | False |
| `-t, --timeout=TIMEOUT` | Optional['int \| None'] | None |

## 示例

```bash
loop 10 curl url
```

# kkp

按端口号终止占用进程

## 用法

用法：`kkp COMMAND | <flags> [ARGS]...`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `by_port` | 终止占用指定端口的进程 |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `ARGS` | 'str' |  |
| `-d, --dry_run=DRY_RUN` | 'bool' | False |

## 示例

```bash
kkp 8080
```

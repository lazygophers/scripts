# unsleep

防止 macOS 系统休眠（指定时长或跟随命令）

## 用法

用法：`unsleep COMMAND | -`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `forever` | 无限制防休眠（Ctrl+C 结束） |
| `timed` | 指定时长防休眠 |
| `with_command` | 跟随命令运行（命令结束即结束防休眠） |

## 示例

```bash
unsleep timed 2h
```

# inject

把 bin/ 注入 shell PATH（写入 ~/.zshrc 等；macOS 可选启用 Touch ID sudo）

## 用法

用法：`inject COMMAND | -`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `run` | 写入 rc 并询问 LAZYGOPHERS AI 配置 |
| `show` | 仅打印将生成的内容，不写盘 |
| `uninstall` | 从 rc 移除 source 行并删除 scripts.sh |

## 示例

```bash
inject
```

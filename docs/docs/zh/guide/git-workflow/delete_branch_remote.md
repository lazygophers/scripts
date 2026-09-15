# delete_branch_remote

删除远端分支（单仓 here / 批量 all）

## 用法

用法：`delete_branch_remote COMMAND | <flags> [ARGS]...`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `all` | 批量扫描所有 Git 仓库删除远端分支 |
| `here` | 仅在当前仓库删除远端分支 |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `ARGS` | 'str' |  |
| `-r, --remote=REMOTE` | 'str' | 'origin' |
| `-y, --yes=YES` | 'bool' | False |

## 示例

```bash
delete_branch_remote <name> [--remote <r>] [-y]
```

# delete_branch

删除本地分支（单仓 here / 批量 all）

## 用法

用法：`delete_branch COMMAND | <flags> [ARGS]...`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `all` | 批量扫描所有 Git 仓库删除指定分支 |
| `here` | 仅在当前仓库删除本地分支 |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `ARGS` | 'str' |  |
| `-f, --force=FORCE` | 'bool' | False |
| `-y, --yes=YES` | 'bool' | False |

## 示例

```bash
delete_branch <name> [--force] [-y]
```

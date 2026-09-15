# sync_branch

批量同步各仓库指定分支到 origin/<branch>

## 用法

用法：`sync_branch COMMAND | <flags>`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `current` | 同步各仓库当前分支（硬对齐 origin/<当前分支>） |
| `to` | 同步指定分支 |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `-f, --force=FORCE` | 'bool' | False |

## 示例

```bash
sync_branch [branch] [--force]
```

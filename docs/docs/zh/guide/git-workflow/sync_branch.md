# sync_branch

批量同步各仓库分支到 origin/<branch>

## 用法

用法：`sync_branch COMMAND | <flags>`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `current` | 双向同步各仓库当前分支：落后快进、领先推上去、分叉先 merge-tree 预演（无冲突自动合并后推送，有冲突跳过） |
| `to` | 硬对齐指定分支：本地领先跳过（`--force` 才 reset 丢弃），落后快进 |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `-f, --force=FORCE` | 'bool' | False |

两种模式工作区有未提交改动（dirty）都会标记 fail。push 被拒（远端并发变动）自动重同步一轮再推，最多 2 轮。

## 示例

```bash
sync_branch current          # 双向同步各仓库当前分支
sync_branch to dev           # 硬对齐 dev 到 origin/dev
sync_branch current --force  # 当前分支也 reset 硬对齐（丢弃本地提交）
```

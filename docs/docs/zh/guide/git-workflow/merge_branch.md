# merge_branch

合并当前分支到指定分支（分支名必填首参）

## 用法

用法：`merge_branch COMMAND | <flags>`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `all` | 批量模式：扫描所有 Git 仓库执行 merge/push → <target> |
| `auto` | 自动判断：cwd 是 git 仓库 → here；否则 → all |
| `here` | 单仓模式：merge/push 当前分支 → <target>（target 由入口名决定） |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `-a, --auto_commit=AUTO_COMMIT` | 'bool' | False |

## 快捷方式

`merge_canary` / `merge_dev` / `merge_develop` / `merge_master` / `merge_test` 都是 `merge_branch` 的固定目标版本——`<target>` 提前写死在命令名里，不用再传分支名首参，其余行为（`all`/`auto`/`here`、`--auto_commit`）完全一致：

| 命令 | 等价于 | 说明 |
| :--- | :--- | :--- |
| `merge_canary` | `merge_branch canary` | 合并当前分支到 canary（单仓 / 批量自动识别） |
| `merge_dev` | `merge_branch dev` | 合并当前分支到 dev（单仓 / 批量自动识别） |
| `merge_develop` | `merge_branch develop` | 合并当前分支到 develop（单仓 / 批量自动识别） |
| `merge_master` | `merge_branch <默认主分支>` | 合并当前分支到默认主分支（master/main 自动识别） |
| `merge_test` | `merge_branch test` | 合并当前分支到 test（单仓 / 批量自动识别） |

## 示例

```bash
merge_branch feature/x   # 通用：合并到任意分支
merge_canary [--dry-run] # 快捷方式：合并到 canary
```

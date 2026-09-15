# push_branch

推送当前分支到指定分支（分支名必填首参）

## 用法

用法：`push_branch COMMAND | <flags>`

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

`push_canary` / `push_dev` / `push_develop` / `push_master` / `push_test` 都是 `push_branch` 的固定目标版本——`<target>` 提前写死在命令名里，不用再传分支名首参，其余行为（`all`/`auto`/`here`、`--auto_commit`、推送后切回原分支）完全一致：

| 命令 | 等价于 | 说明 |
| :--- | :--- | :--- |
| `push_canary` | `push_branch canary` | 推送当前分支到 canary 后切回原分支（单仓 / 批量） |
| `push_dev` | `push_branch dev` | 推送当前分支到 dev 后切回原分支（单仓 / 批量） |
| `push_develop` | `push_branch develop` | 推送当前分支到 develop 后切回原分支（单仓 / 批量） |
| `push_master` | `push_branch <默认主分支>` | 推送当前分支到默认主分支后切回原分支（单仓 / 批量） |
| `push_test` | `push_branch test` | 推送当前分支到 test 后切回原分支（单仓 / 批量） |

## 示例

```bash
push_branch feature/x   # 通用：推送到任意分支
push_canary [--stay]    # 快捷方式：推送到 canary
```

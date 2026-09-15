# merge_develop

合并当前分支到 develop（单仓 / 批量自动识别）

## 用法

用法：`merge_develop COMMAND | <flags>`

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

## 示例

```bash
merge_develop
```

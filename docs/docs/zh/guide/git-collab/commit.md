# commit

自动提交变更（调 claude 生成 message；单仓或批量扫描子目录）

## 用法

用法：`commit COMMAND | <flags> [ARGS]...`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `all` | 批量扫描当前目录所有 git 仓库并提交 |
| `auto` | 智能判断：cwd 是 git 仓库 → here；否则 → all |
| `here` | 在当前 git 仓库提交 |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `ARGS` | 'str' |  |
| `-d, --dry_run=DRY_RUN` | 'bool' | False |
| `-s, --settings=SETTINGS` | Optional['str \| None'] | None |

## 示例

```bash
commit
```

# 脚本功能

## 安装

```bash
./bin/inject            # 生成 ~/.scripts.sh 并 source 到所有 rc
./bin/inject --show     # 预览将写入的内容
./bin/inject --uninstall  # 卸载
```

inject 幂等：重跑不会重复追加。完成后重启 shell 或 `source ~/.zshrc` 即可在任意目录直接调用。

## 功能表

| 脚本             | 功能                                                         | 示例                          |
| :--------------- | :----------------------------------------------------------- | :---------------------------- |
| `checkwork`      | 自动化编译检查 + 语音通知                                    | `checkwork`                   |
| `cpd`            | 深度覆盖复制 (默认只新增/更新; `-f` 删除目标多余文件)        | `cpd src/* dest/`             |
| `kk`             | 按进程名终止进程                                             | `kk nginx`                    |
| `kkp`            | 按端口终止进程                                               | `kkp 8080`                    |
| `n`              | macOS 语音播报 (`say`)                                       | `n "构建完成"`                |
| `loop`           | 循环执行命令, 追踪成功/失败                                  | `loop 10 curl url`            |
| `merge_canary`   | 合并当前分支 → canary, 留在 canary                           | `merge_canary [--dry-run]`     |
| `merge_develop`  | 合并当前分支 → develop, 留在 develop                         | `merge_develop`                |
| `merge_master`     | 合并当前分支 → 主分支(master/main 自动识别), 留在目标                        | `merge_master`                   |
| `merge_test`     | 合并当前分支 → test, 留在 test                               | `merge_test`                   |
| `push_canary`    | 合并当前分支 → canary, 推送后切回原分支                      | `push_canary [--stay]`         |
| `push_develop` / `push_master` / `push_test` | 同上, 目标分别为 develop / 主分支(自动识别) / test      |                               |
| `push_*` (批量)  | 在非 git 目录执行 push_* 时自动批量: 扫描子目录 Git 仓库逐个推送 | `push_canary [--dry-run]` |
| `switch_branch`  | 批量切换分支 (不存在则从主分支(自动识别)创建)                 | `switch_branch <branch>`      |
| `sync_master`    | 批量同步 master                                              | `sync_master`                 |
| `sync_branch`    | 批量同步当前分支 (或指定分支) 到 origin/<branch>                             | `sync_branch [branch] [--force]` |
| `delete_branch` | 删除本地分支 (单仓/批量) | `delete_branch <name> [--force] [-y]` |
| `delete_branch_remote` | 删除远端分支 (单仓/批量) | `delete_branch_remote <name> [--remote <r>] [-y]` |
| `fetch_all`  | 批量 fetch 所有 Git 仓库                                     | `fetch_all`               |
| `unsleep`        | macOS caffeinate 防休眠                                      | `unsleep -t 3600`             |
| `reindex`        | 项目重新索引 (local-only, .gitignore)                        | `reindex`                     |
| `inject`         | 把 bin/ 注入 shell PATH                                      | `inject`                      |

## 迁移说明（旧名已移除）

- `mergec` / `mergedev` / `mergem` / `merget` → `merge_canary` / `merge_develop` / `merge_master` / `merge_test`
- `pushc` / `pushdev` / `pushm` / `pusht` → `push_canary` / `push_develop` / `push_master` / `push_test`
- `pushc_all` 已并入 `push_*`：在非 git 目录执行即自动批量，自动执行无确认，`--dry-run` 预览。

## 环境变量

- `BATCH_CONCURRENCY`：批量操作（`push_*` / `switch_branch` / `sync_branch` / `sync_master`）并行并发上限，默认 `4`。例：`BATCH_CONCURRENCY=8 push_canary`。

## 环境依赖

- **Python 3.10+**（薄壳与核心逻辑）
- **Git**（merge_* / push_* / switch_branch / sync_master / fetch_all / delete_branch）
- **macOS**（`n` 用 `say`，`unsleep` 用 `caffeinate`）
- **rich**（输出美化，`pip install rich`）
- **pgrep / ps / lsof / kill**（kk / kkp）

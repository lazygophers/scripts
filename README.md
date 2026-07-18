# scripts

[简体中文](README.md) | [English](README.en.md) | [Français](README.fr.md) | [Español](README.es.md) | [Русский](README.ru.md) | [العربية](README.ar.md)

开发效率工具集 — 各种快捷脚本的集合。Bash/Python 混合薄壳入口, 核心逻辑沉淀在 `lib/`。

---

## 安装: 把 bin/ 注入 PATH

```bash
./bin/inject            # 生成 ~/.scripts.sh 并 source 到所有 rc (~/.bashrc / ~/.zshrc / ~/.profile / ~/.bash_profile)
./bin/inject --show     # 预览将写入的内容
./bin/inject --uninstall  # 卸载
```

inject 幂等: 重跑不会重复追加。完成后重启 shell 或 `source ~/.zshrc` 即可在任意目录直接调用 `checkwork` / `merge_canary` / ...。

---

## 脚本功能

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
| `merge_master`   | 合并当前分支 → 主分支(master/main 自动识别), 留在目标       | `merge_master`                   |
| `merge_test`     | 合并当前分支 → test, 留在 test                               | `merge_test`                   |
| `push_canary`    | 合并当前分支 → canary, 推送后切回原分支                      | `push_canary [--stay]`         |
| `push_develop` / `push_master` / `push_test` | 同上, 目标分别为 develop / 主分支(自动识别) / test      |                               |
| `push_branch`    | 批量推送当前分支到远端                                       | `push_branch`                  |
| `push_*` (批量)  | 在非 git 目录执行 push_* 时自动批量: 扫描子目录 Git 仓库逐个推送 | `push_canary [--dry-run]` |
| `commit`         | 自动提交变更 (调 claude 生成 message)                        | `commit`                       |
| `issue`          | 自动创建 Issue (调 claude 生成 title/body)                   | `issue`                        |
| `mr`             | 自动创建 PR/MR (调 claude 生成 title/body, 默认 draft)       | `mr [base]`                   |
| `squash_pr`      | 压缩 source 为单 commit → 对接 mr 开 PR                     | `squash_pr [source] <target>`  |
| `switch_branch`  | 批量切换分支 (不存在则从主分支(自动识别)创建)                 | `switch_branch <branch>`      |
| `sync_branch`    | 批量同步当前分支 (或指定分支) 到 origin/<branch>             | `sync_branch [branch] [--force]` |
| `delete_branch` | 删本地分支 (单仓;非 git 目录批量) | `delete_branch <name> [--force] [-y]` |
| `delete_branch_remote` | 删远端分支 (单仓;非 git 目录批量) | `delete_branch_remote <name> [--remote <r>] [-y]` |
| `sync_master`    | 批量同步 master (= `sync_branch master`)                     | `sync_master`                 |
| `fetch_all`      | 批量 fetch 所有 Git 仓库                                     | `fetch_all`                   |
| `list_branches`  | 列出本地分支（单仓或扫描所有 Git 仓库，跨仓同名分支标 ⟱）   | `list_branches`               |
| `unsleep`        | macOS caffeinate 防休眠                                      | `unsleep -t 3600`             |
| `inject`         | 把 bin/ 注入 shell PATH                                      | `inject`                      |

> **迁移说明（旧名已移除）**：原 `mergec/mergedev/mergem/merget` → `merge_canary/merge_develop/merge_master/merge_test`；`pushc/pushdev/pushm/pusht` → `push_canary/push_develop/push_master/push_test`；`pushc_all` 已并入 `push_*`（在非 git 目录执行即自动批量，自动执行无确认，`--dry-run` 预览）。

> **环境变量**：`BATCH_CONCURRENCY` 控制批量操作（`push_*` / `switch_branch` / `sync_branch` / `sync_master`）并行并发上限，默认 `4`。例：`BATCH_CONCURRENCY=8 push_canary`。

---

## 文档

完整文档站：https://lazygophers.github.io/scripts/

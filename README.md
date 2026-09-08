# scripts

[简体中文](README.md) | [English](README.en.md) | [Français](README.fr.md) | [Español](README.es.md) | [Русский](README.ru.md) | [العربية](README.ar.md)

开发效率工具集 — 各种快捷脚本的集合。`bin/` 只是薄壳, 实现全在 `lib/cli/`, 共享能力在 `lib/`。

---

## 免安装: 直接从 GitHub 跑

```bash
uvx git+https://github.com/lazygophers/scripts                     # 列出全部工具（等同 lazyhelp）
uvx --from git+https://github.com/lazygophers/scripts checkwork    # 跑其中任意一个
```

`uvx` 是 [uv](https://docs.astral.sh/uv/) 自带的命令：临时下载一个工具跑一次，跑完不留痕，不用 clone 仓库。不带命令名时跑的是与包同名的 `scripts` 入口，它就是 `lazyhelp`。带 `--from` 时 uv 强制要求写命令名。

常用就装到本地（`uv tool install` 是常驻安装，命令一直留在 PATH 上）：

```bash
uv tool install git+https://github.com/lazygophers/scripts
```

---

## 安装: 把 bin/ 注入 PATH

```bash
./bin/inject            # 生成 ~/.scripts.sh + completion，并 source 到所有 rc (~/.bashrc / ~/.zshrc / ~/.profile / ~/.bash_profile)
./bin/inject show     # 预览将写入的内容
./bin/inject uninstall  # 卸载
```

inject 幂等: 重跑不会重复追加。完成后重启 shell 或 `source ~/.zshrc` 即可在任意目录直接调用 `checkwork` / `merge_canary` / ...；同时会装好 zsh/bash/fish completion。

---

## 脚本功能

按用途分七类。终端速查: `lazyhelp`；单工具完整用法: `<工具> --help`；AI 向指引: `<工具> --skills`。

### Git 工作流

| 脚本 | 功能 | 示例 |
| :--- | :--- | :--- |
| `merge_canary` | 合并当前分支 → canary, 留在 canary | `merge_canary [--dry-run]` |
| `merge_develop` / `merge_dev` / `merge_test` | 同上, 目标分别为 develop / dev / test | `merge_develop` |
| `merge_master` | 合并当前分支 → 主分支(master/main 自动识别), 留在目标 | `merge_master` |
| `merge_branch` | 合并当前分支 → 指定分支(分支名必填首参) | `merge_branch feature/x` |
| `push_canary` | 合并当前分支 → canary, 推送后切回原分支 | `push_canary [--stay]` |
| `push_develop` / `push_dev` / `push_test` | 同上, 目标分别为 develop / dev / test |  |
| `push_master` | 同上, 目标为主分支(自动识别) |  |
| `push_branch` | 推当前分支到指定分支(分支名必填首参) | `push_branch feature/x` |
| `switch_branch` | 批量切换分支 (不存在则从主分支自动识别创建) | `switch_branch <branch>` |
| `sync_branch` | 批量同步当前分支 (或指定分支) 到 origin/<branch> | `sync_branch [branch] [--force]` |
| `sync_master` | 批量同步主分支(自动识别) | `sync_master` |
| `delete_branch` | 删本地分支 (单仓;非 git 目录批量) | `delete_branch <name> [--force] [-y]` |
| `delete_branch_remote` | 删远端分支 (单仓;非 git 目录批量) | `delete_branch_remote <name> [--remote <r>] [-y]` |

> 在非 git 目录执行以上命令时自动批量: 扫描子目录 Git 仓库逐个执行。

### Git 协作

| 脚本 | 功能 | 示例 |
| :--- | :--- | :--- |
| `commit` | 自动提交变更 (调 claude 生成 message) | `commit` |
| `mr` | 自动创建 PR/MR (调 claude 生成 title/body, 默认 draft) | `mr [base]` |
| `issue` | 自动创建 Issue (调 claude 生成 title/body) | `issue` |
| `squash_pr` | 压缩 source 为单 commit → 对接 mr 开 PR | `squash_pr [source] <target>` |
| `fetch_all` | 批量 fetch 所有 Git 仓库 | `fetch_all` |
| `list_branch` | 列出本地分支(单仓或扫描所有 Git 仓库, 跨仓同名分支标 ⟱) | `list_branch` |

### 构建与检查

| 脚本 | 功能 | 示例 |
| :--- | :--- | :--- |
| `checkwork` | push 前多语言编译检查闸门 (Go/Rust/Python/Java/Node) + 语音通知 | `checkwork` |
| `check_ai` | AI API 端点连通性检测 (空 POST) | `check_ai` |
| `cicd` | 轮询当前分支 CI/CD, 完成后输出最终结果 | `cicd` |

### 数据与网络

| 脚本 | 功能 | 示例 |
| :--- | :--- | :--- |
| `archery` | Archery SQL 平台 CLI (查询/上线工单, 按域名分别登录) | `archery query execute 'select 1' --instance-name prod --db-name orders` |
| `grafana` | Grafana HTTP API CLI (按域名分别登录) | `grafana health` |
| `ovpn` | OpenVPN 客户端 (自动填账密与二步验证码, 支持分流) | `ovpn connect` |
| `vpn-prio` | 调整 macOS 网络服务优先级 (压低 OpenVPN 默认路由) | `vpn-prio --help` |
| `ipinfo` | 查询内网 IP + 网络类型 (含热点识别) | `ipinfo` |
| `disable-ipv6` / `enable-ipv6` | 关闭/开启本机所有网络服务的 IPv6 (需 sudo) | `sudo disable-ipv6` |

### 网页检索

| 脚本 | 功能 | 示例 |
| :--- | :--- | :--- |
| `websearch` | 多引擎网页检索 (全引擎免 key 并行, 按 URL 合并去重) | `websearch rust async` |
| `webgrab` | 抓网页转 Markdown (反爬直抓 + Playwright 渲染 + 34 站点适配 + 登录态持久化) | `webgrab https://example.com` |

### 进程与运行

| 脚本 | 功能 | 示例 |
| :--- | :--- | :--- |
| `kk` | 按进程名终止进程 | `kk nginx` |
| `kkp` | 按端口终止进程 | `kkp 8080` |
| `loop` | 循环执行命令, 追踪成功/失败 | `loop 10 curl url` |
| `unsleep` | macOS caffeinate 防休眠 | `unsleep timed 2h` |

### 文件与系统

| 脚本 | 功能 | 示例 |
| :--- | :--- | :--- |
| `cpd` | 深度覆盖复制 (默认只新增/更新; `-f` 删除目标多余文件) | `cpd src/* dest/` |
| `n` | macOS 语音播报 (`say`) | `n "构建完成"` |
| `inject` | 把 bin/ 注入 shell PATH | `inject` |
| `graphwatch` | graphify 守护服务: 注册目录自动重建知识图谱 | `graphwatch add <dir>` |
| `lazyhelp` | 终端工具目录速查 + 转发 `--help` | `lazyhelp help <tool>` |

> **迁移说明（旧名已移除）**：原 `mergec/mergedev/mergem/merget` → `merge_canary/merge_develop/merge_master/merge_test`；`pushc/pushdev/pushm/pusht` → `push_canary/push_develop/push_master/push_test`；`pushc_all` 已并入 `push_*`（在非 git 目录执行即自动批量，自动执行无确认，`--dry-run` 预览）。

> **环境变量**：`BATCH_CONCURRENCY` 控制批量操作（`push_*` / `switch_branch` / `sync_branch` / `sync_master`）并行并发上限，默认 `4`。例：`BATCH_CONCURRENCY=8 push_canary`。
>
> **通用选项 `--no-say`**：所有 `bin/*`（除 `n` 本身）支持 `--no-say` 静音 macOS 语音播报；等价于 `SCRIPTS_NO_SAY=1`。例：`delete_branch --no-say hotfix/x`、`push_canary --no-say`。
>
> **`push_*` 选项 `--no-check`**：跳过 checkwork 构建检查闸门（当前分支预检 + 合并结果检），其余流程不变。例：`push_canary --no-check`。

---

## 文档

完整文档站：https://lazygophers.github.io/scripts/

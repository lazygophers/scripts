---
name: lazyscripts
description: 本仓库（lazygophers scripts）bin/ 下全部常用脚本的合计入口：git 工作流（merge_*/push_*/switch_branch/sync_*/delete_branch）、PR/Issue 工具、CI/CD、编译检查、Archery 数据库、Grafana、OpenVPN、网页抓取、进程/文件/系统工具——覆盖用户日常所需的全部脚本与命令行工具。任何要「合并分支、推送、提交、开 PR、看 CI、跑 SQL、连 VPN、抓网页、杀进程、复制目录、防休眠」的任务先用这里的工具，代替手写命令。
---

# scripts

`bin/` 共 40+ 工具，覆盖日常开发全部场景。前提：已 `./bin/inject` 注入 PATH，或直接 `./bin/<name>` 运行。

按场景挑文件读：

| 场景 | 文件 |
|------|------|
| 合并/推送当前分支、批量切分支、删分支、fetch | [git-workflow.md](git-workflow.md) |
| 自动提交、建 Issue、开 PR/MR、squash PR | [pr-tools.md](pr-tools.md) |
| 看/等 CI/CD 状态与失败日志 | [cicd.md](cicd.md) |
| push 前多语言编译检查、AI 端点探测 | [checkwork.md](checkwork.md) |
| Archery 数据库查询、SQL 上线工单 | [archery.md](archery.md) |
| Grafana API 查仪表盘 | [grafana.md](grafana.md) |
| 连/断 OpenVPN、分流规则、VPN 路由问题 | [ovpn.md](ovpn.md) |
| 抓网页转 Markdown（带反爬处理） | [webgrab.md](webgrab.md) |
| 杀进程、深度复制、循环重试、防休眠、语音播报、IP/网络 | [system-utils.md](system-utils.md) |

查目录：`lazyhelp`（人类速查表）；`<工具> --skills`（AI 向指引，含可照抄示例）；`lazyhelp help <工具>`（完整 --help）。

## 失败分支（如果 X 失败 → 做 Y）

| 症状 | 一线修复 | 仍失败兜底 |
|------|---------|-----------|
| `command not found: merge_master` 等 | `./bin/inject` 注入 PATH，或直接 `./bin/<name>` 运行 | `inject show` 预览将写入的内容，确认后 `inject run` |
| 工具行为和分文件描述不一致 | 重跑并加 `--debug`（打印成功命令的完整输出） | `lazyhelp help <工具>` 看当前版本的完整 --help |
| 批量脚本卡在某个仓库 | 进那个仓库单独跑单仓子命令（多为 `here`） | 查对应分文件「要点」段的回滚说明 |
| push/fetch 类网络超时 | 直接重跑（内置指数退避重试） | `ipinfo` 查网络；VPN 干扰路由用 `vpn-prio` |

## 通用约定（全部 bin/*）

- `--no-say` / `SCRIPTS_NO_SAY=1` 静音 macOS 语音通知。
- `--debug` / `SCRIPTS_DEBUG=1` 打印成功命令的输出（默认只显示失败）。
- 有 `--dry-run` 的工具先预览再执行。

## 不要做（反例黑名单）

- **别为分支合并/推送手写 git 命令**——工作流脚本自带冲突干跑、checkwork 闸门、失败回滚，手写全部绕过。
- **别在目标分支上跑 `merge_*` / `push_*`**（在 canary 上跑 `push_canary`）——脚本会拦，别试图绕。
- **破坏性操作别跳过预览**：`delete_branch_remote`、`squash_pr`（可能 force-push）、`cpd -f`（删目标多余文件）——先 dry-run / 列出影响面。
- **`archery show` / `archery code` / `ovpn show` 输出密钥**——别原样贴进对话、日志或工单。
- **cicd 禁止手动触发流水线**——只能 `cicd play` 点已有流水线里的 manual job。
- **webgrab 遇到交互式人机验证/Turnstile**——不会自动过，如实报错，别反复重试硬闯。

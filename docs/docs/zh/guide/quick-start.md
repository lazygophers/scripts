# 快速开始

## 免安装: 直接从 GitHub 跑

```bash
uvx git+https://github.com/lazygophers/scripts                     # 列出全部工具（等同 lazyhelp）
uvx --from git+https://github.com/lazygophers/scripts checkwork    # 跑其中任意一个
uv tool install git+https://github.com/lazygophers/scripts         # 常驻安装，命令留在 PATH 上
```

`uvx` 是 [uv](https://docs.astral.sh/uv/) 自带的命令：临时下载一个工具跑一次，跑完不留痕，不用 clone 仓库。不带命令名时跑的是与包同名的 `scripts` 入口，它就是 `lazyhelp`；带 `--from` 时 uv 强制要求写命令名。

## 常驻安装

```bash
./bin/inject            # 生成 ~/.scripts.sh 并 source 到所有 rc
./bin/inject show     # 预览将写入的内容
./bin/inject uninstall  # 卸载
```

inject 幂等：重跑不会重复追加。完成后重启 shell 或 `source ~/.zshrc` 即可在任意目录直接调用。

macOS 上 inject 还会询问是否启用 Touch ID sudo 授权（指纹优先，失败回落密码）：确认后写一行 `auth sufficient pam_tid.so` 到 `/etc/pam.d/sudo_local`（Apple 预留的本地覆盖点，系统升级不冲掉；只对本地图形会话生效，SSH 远程回落密码）。

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

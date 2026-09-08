# 简介

`scripts` 是开发效率工具集 — 常见开发与运维任务的快捷脚本。Bash/Python 薄壳入口，核心逻辑沉淀在 `lib/`。

## 特点

- **薄壳入口**: `bin/` 下脚本只有 3 行 path hack + import，业务逻辑全部在 `lib/` 扁平模块里。
- **按用途分类**: Git 工作流 / Git 协作 / 构建与检查 / 数据与网络 / 网页检索 / 进程与运行 / 文件与系统，终端 `lazyhelp` 一页速查。
- **批量操作**: `merge_*` / `push_*` / `switch_branch` / `sync_master` 单仓与多仓批量一套命令。
- **安全优先**: 进程管理自排除，Git 操作前工作区清洁检查与回滚。

## 快速开始

```bash
./bin/inject            # 把 bin/ 注入 shell PATH
```

之后重启 shell，即可在任意目录调用 `checkwork` / `merge_canary` / ...

见[脚本功能](./scripts.md)与 GitHub 仓库 [lazygophers/scripts](https://github.com/lazygophers/scripts)。

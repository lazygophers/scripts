# 简介

`scripts` 是开发效率工具集 — 常见开发与运维任务的快捷脚本。`bin/` 只是薄壳，实现全在 `lib/cli/`，共享能力在 `lib/`。

## 特点

- **薄壳入口**: `bin/` 下每个脚本都是同一个壳子（改路径 + `from lib.cli.<模块> import main`），没有业务逻辑，也没有 symlink；实现在 `lib/cli/<名>.py`，共享能力在 `lib/` 扁平模块里。
- **免安装远程执行**: 全部命令都注册成了 `[project.scripts]`，`uvx git+https://github.com/lazygophers/scripts` 不用 clone 就能跑。
- **按用途分类**: Git 工作流 / Git 协作 / 构建与检查 / 数据与网络 / 网页检索 / 进程与运行 / 文件与系统，终端 `lazyhelp` 一页速查。
- **批量操作**: `merge_*` / `push_*` / `switch_branch` / `sync_master` 单仓与多仓批量一套命令。
- **安全优先**: 进程管理自排除，Git 操作前工作区清洁检查与回滚。

## 快速开始

不想装，跑一次就走：

```bash
uvx git+https://github.com/lazygophers/scripts                     # 列出全部工具
uvx --from git+https://github.com/lazygophers/scripts checkwork    # 跑其中任意一个
```

要常驻在本机：

```bash
./bin/inject            # 把 clone 下来的 bin/ 注入 shell PATH
```

之后重启 shell，即可在任意目录调用 `checkwork` / `merge_canary` / ...

见[脚本功能](./scripts.md)与 GitHub 仓库 [lazygophers/scripts](https://github.com/lazygophers/scripts)。

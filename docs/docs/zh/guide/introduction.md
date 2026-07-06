# 简介

`scripts` 是开发效率工具集 —— 各种快捷脚本的集合。Bash/Python 混合薄壳入口，核心逻辑沉淀在 `lib/`。

## 特点

- **薄壳入口**：`bin/` 下脚本仅 3 行 path hack + import，业务逻辑全部在 `lib/commands/` 下。
- **域分类**：`build` / `file` / `git` / `process` / `misc` / `system`，互不干扰。
- **批量操作**：`merge_*` / `push_*` / `switch_branch` / `sync_master` 支持单仓与多仓批量。
- **安全优先**：进程管理自排除、Git 操作前工作区清洁检查与回滚。

## 上手

```bash
./bin/inject            # 把 bin/ 注入 shell PATH
```

完成后重启 shell，即可在任意目录直接调用 `checkwork` / `merge_canary` / ...。

详见 [脚本功能](./scripts.md) 与 GitHub 仓库 [lazygophers/scripts](https://github.com/lazygophers/scripts)。

# 目录结构

```
scripts/
├── bin/                          # 薄壳入口脚本 (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_* / push_*          # 12 个真实文件，各自调 lib/cli/gitwf.py 里同名入口函数
│   ├── switch_branch, sync_master, sync_branch, fetch_all, delete_branch, delete_branch_remote
│   ├── loop, unsleep, websearch, webgrab, archery, grafana, ovpn, ...
│   └── inject                    # 把 bin/ 注入 shell PATH
├── lib/                          # 全部核心逻辑（扁平，不用子目录）
│   ├── {名}.py                   # 每个命令的业务模块（git_workflow / batch_git / build / ...）
│   ├── fire_base.py              # BaseCli + run_cli + timed_cli，薄壳统一骨架
│   ├── lazyhelp.py               # 工具目录注册表（TOOLS = 名称 → 分类 + 一句话功能）
│   ├── skills_help.py            # --skills 的 AI 向指引（COMMAND_SKILLS）
│   └── ui / notify / exec / process   # 共享库，跨命令复用
├── skills/lazyscripts/           # AI skill 索引（SKILL.md + 按场景分文件）
├── docs/                         # Rspress 文档站（六语言源在 docs/docs/<lang>/）
├── tests/                        # unittest 套件
└── README.md（+ 5 个语言译本）
```

## 调用链

```
bin/{脚本}            (3 行 path hack + import)
  → run_cli(<名>Cli())      # lib/fire_base.py，fire 子命令分发
    → lib/{名}.py 的业务函数
      → 共享 lib/ui.py / lib/exec.py / ...
```

`bin/` 里**没有业务逻辑，也没有 symlink**：每个薄壳都是 `from lib.cli.<模块> import <函数> as main` + `raise SystemExit(main())`。实现放在 `lib/cli/<名>.py`（一个命令一个模块），再去调共享的 `lib/{域}.py`。`merge_*` / `push_*` 是 `lib/cli/gitwf.py` 上的 12 个薄壳，各自显式传 `(name, action, target)`，不再靠 argv[0] 猜。同一批函数也注册成 `[project.scripts]`，所以 `uvx --from git+https://github.com/lazygophers/scripts <名>` 不用 clone 就能跑任意工具。

新增公开工具要在 `lib/lazyhelp.py` 的 `TOOLS` 注册（分类 + 一句话功能），有 AI 使用指引就同步 `lib/skills_help.py` 的 `COMMAND_SKILLS`。

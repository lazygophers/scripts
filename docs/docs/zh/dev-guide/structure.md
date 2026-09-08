# 目录结构

```
scripts/
├── bin/                          # 薄壳入口脚本 (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_* / push_*          # 全部 symlink → bin/_gitwf，按入口名分发
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

薄壳只负责把 argv 交给 `run_cli`，**不写业务逻辑**。`merge_*` / `push_*` 是指向 `bin/_gitwf` 的 symlink，由 argv[0] 的文件名决定 action 与目标分支。共享能力（git 操作、命令执行、UI、通知、构建检测、进程管理……）沉淀在 `lib/{域}.py`，跨命令复用。

新增公开工具要在 `lib/lazyhelp.py` 的 `TOOLS` 注册（分类 + 一句话功能），有 AI 使用指引就同步 `lib/skills_help.py` 的 `COMMAND_SKILLS`。

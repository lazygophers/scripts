# 目录结构

```
scripts/
├── bin/                          # 薄壳入口脚本 (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_canary, merge_develop, merge_auto, merge_test   # 调 lib git_workflow.merge_to(target)
│   ├── push_canary, push_develop, push_auto, push_test       # 单仓 push_to / 非 git 目录自动批量
│   ├── switch_branch, sync_master, sync_branch, fetch_all, delete_branch, delete_branch_remote
│   ├── loop, unsleep, reindex
│   └── inject                    # 把 bin/ 注入 shell PATH
├── lib/
│   ├── commands/{域}/{命令}.py    # 每个命令的业务逻辑, 暴露 main(argv) -> int
│   │   ├── build/  file/  git/  process/  misc/  system/
│   │   └── git/merge.py + git/push.py 额外暴露 run(target, argv)
│   └── {域}.py                    # 共享库 (git/exec/ui/notify/build/process/...)
├── tests/                        # unittest 套件
├── commit / mr / issue          # bash 脚本, 待重写为 py (临时留根目录)
└── README.md
```

## 调用链

```
bin/{脚本}            (3 行 path hack + import)
  → lib.commands.{域}.{命令}.main(argv)
    → 共享 lib/{域}.py
```

薄壳只负责把 argv 透传到业务模块，**不写业务逻辑**。共享能力（git 操作、命令执行、UI、通知、构建检测、进程管理……）沉淀在 `lib/{域}.py`，跨命令复用。

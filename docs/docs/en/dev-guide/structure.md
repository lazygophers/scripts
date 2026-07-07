# Structure

```
scripts/
├── bin/                          # thin entrypoint scripts (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_canary, merge_develop, merge_auto, merge_test   # call lib git_workflow.merge_to(target)
│   ├── push_canary, push_develop, push_auto, push_test       # single-repo push_to / auto-batch in non-git dir
│   ├── switch_branch, sync_master, sync_branch, fetch_all
│   ├── loop, unsleep, reindex
│   └── inject                    # inject bin/ into shell PATH
├── lib/
│   ├── commands/{domain}/{command}.py   # business logic per command, exposes main(argv) -> int
│   │   ├── build/  file/  git/  process/  misc/  system/
│   │   └── git/merge.py + git/push.py also expose run(target, argv)
│   └── {domain}.py                     # shared libs (git/exec/ui/notify/build/process/...)
├── tests/                        # unittest suite
├── commit / prc / issue          # bash scripts, pending py rewrite (temporarily at repo root)
└── README.md
```

## Call Chain

```
bin/{script}          (3-line path hack + import)
  → lib.commands.{domain}.{command}.main(argv)
    → shared lib/{domain}.py
```

The thin entrypoint only forwards argv to the business module — **no business logic here**. Shared capabilities (git ops, command exec, UI, notifications, build detection, process management, ...) live in `lib/{domain}.py` and are reused across commands.

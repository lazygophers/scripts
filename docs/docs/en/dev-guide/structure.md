# Structure

```
scripts/
├── bin/                          # thin entrypoint scripts (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_* / push_*          # all symlinks → bin/_gitwf, dispatched by entry name
│   ├── switch_branch, sync_master, sync_branch, fetch_all, delete_branch, delete_branch_remote
│   ├── loop, unsleep, websearch, webgrab, archery, grafana, ovpn, ...
│   └── inject                    # inject bin/ into shell PATH
├── lib/                          # all core logic (flat, no subdirs)
│   ├── {name}.py                 # business module per command (git_workflow / batch_git / build / ...)
│   ├── fire_base.py              # BaseCli + run_cli + timed_cli, the common thin-shell skeleton
│   ├── lazyhelp.py               # tool registry (TOOLS = name → category + one-line description)
│   ├── skills_help.py            # AI-facing --skills guidance (COMMAND_SKILLS)
│   └── ui / notify / exec / process   # shared libs, reused across commands
├── skills/lazyscripts/           # AI skill index (SKILL.md + per-topic files)
├── docs/                         # Rspress docs site (six-language sources in docs/docs/<lang>/)
├── tests/                        # unittest suite
└── README.md (+ 5 translations)
```

## Call Chain

```
bin/{script}            (3-line path hack + import)
  → run_cli(<Name>Cli())      # lib/fire_base.py, fire subcommand dispatch
    → business function in lib/{name}.py
      → shared lib/ui.py / lib/exec.py / ...
```

The thin entrypoint only hands argv to `run_cli` — **no business logic here**. `merge_*` / `push_*` are symlinks to `bin/_gitwf`; argv[0]'s basename decides action and target branch. Shared capabilities (git ops, command exec, UI, notifications, build detection, process management, ...) live in `lib/{domain}.py` and are reused across commands.

New public tools must be registered in `TOOLS` of `lib/lazyhelp.py` (category + one-line description); AI guidance goes into `COMMAND_SKILLS` of `lib/skills_help.py`.

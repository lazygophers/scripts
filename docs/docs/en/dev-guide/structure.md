# Structure

```
scripts/
├── bin/                          # thin entrypoint scripts (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_* / push_*          # 12 real files, each calling its own lib/cli/gitwf.py entry
│   ├── switch_branch, sync_master, sync_branch, fetch_all, delete_branch, delete_branch_remote
│   ├── loop, unsleep, websearch, webgrab, archery, grafana, ovpn, ...
│   └── inject                    # inject bin/ into shell PATH
├── lib/                          # all core logic
│   ├── cli/{name}.py             # the CLI of one command (what used to sit in bin/)
│   ├── cli/gitwf.py              # shared merge_*/push_* implementation + 12 entry functions
│   ├── cli/ipv6.py               # disable-ipv6 / enable-ipv6 (Python, no longer bash)
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
bin/{script}                       # cloned repo: 3-line shell
  or the installed `{script}` command  # uvx / uv tool install: [project.scripts]
    → lib/cli/{script}.py: main()
      → run_cli(<Name>Cli())         # lib/fire_base.py, fire subcommand dispatch
        → business function in lib/{name}.py
          → shared lib/ui.py / lib/exec.py / ...
```

`bin/` holds **no business logic and no symlinks**: every shell is `from lib.cli.<module> import <fn> as main` + `raise SystemExit(main())`. The implementation is `lib/cli/<name>.py` (one module per command), which calls the shared `lib/{domain}.py` helpers. `merge_*` / `push_*` are 12 shells over `lib/cli/gitwf.py`; each passes `(name, action, target)` explicitly instead of sniffing argv[0]. The same functions are registered as `[project.scripts]`, so `uvx --from git+https://github.com/lazygophers/scripts <name>` runs any tool without cloning. Running `uvx git+https://github.com/lazygophers/scripts` with no command name hits the `scripts` entry point, an alias of `lazyhelp`.

New public tools must be registered in `TOOLS` of `lib/lazyhelp.py` (category + one-line description); AI guidance goes into `COMMAND_SKILLS` of `lib/skills_help.py`.

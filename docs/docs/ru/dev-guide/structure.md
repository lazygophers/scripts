# Структура

```
scripts/
├── bin/                          # тонкие обёртки (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_* / push_*          # 12 обычных файлов, каждый зовёт свою точку входа в lib/cli/gitwf.py
│   ├── switch_branch, sync_master, sync_branch, fetch_all, delete_branch, delete_branch_remote
│   ├── loop, unsleep, websearch, webgrab, archery, grafana, ovpn, ...
│   └── inject                    # внедряет bin/ в PATH оболочки
├── lib/                          # вся логика (плоско, без подпапок)
│   ├── {имя}.py                  # бизнес-модуль на команду (git_workflow / batch_git / build / ...)
│   ├── fire_base.py              # BaseCli + run_cli + timed_cli, общий каркас обёрток
│   ├── lazyhelp.py               # реестр инструментов (TOOLS = имя → категория + описание)
│   ├── skills_help.py            # ИИ-руководство --skills (COMMAND_SKILLS)
│   └── ui / notify / exec / process   # общие библиотеки
├── skills/lazyscripts/           # индекс AI-skill (SKILL.md + файлы по темам)
├── docs/                         # сайт документации Rspress (6 языков в docs/docs/<lang>/)
├── tests/                        # набор unittest
└── README.md (+ 5 переводов)
```

## Цепочка вызовов

```
bin/{скрипт}            (3 строки path-хака + import)
  → run_cli(<Имя>Cli())      # lib/fire_base.py, диспетчер подкоманд fire
    → бизнес-функция в lib/{имя}.py
      → общие lib/ui.py / lib/exec.py / ...
```

В `bin/` **нет ни бизнес-логики, ни symlink'ов**: каждая обёртка — это `from lib.cli.<модуль> import <fn> as main` + `raise SystemExit(main())`. Реализация лежит в `lib/cli/<имя>.py` (один модуль на команду) и вызывает общие помощники из `lib/{домен}.py`. `merge_*` / `push_*` — 12 обёрток над `lib/cli/gitwf.py`, каждая явно передаёт `(name, action, target)`. Те же функции зарегистрированы как `[project.scripts]`, поэтому `uvx --from git+https://github.com/lazygophers/scripts <имя>` запускает любой инструмент без клонирования.

Новый публичный инструмент регистрируется в `TOOLS` из `lib/lazyhelp.py`; ИИ-руководство — в `COMMAND_SKILLS` из `lib/skills_help.py`.

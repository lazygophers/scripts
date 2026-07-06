# Структура каталогов

```
scripts/
├── bin/                          # Скрипты лёгкого входа (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_canary, merge_develop, merge_auto, merge_test   # вызывает lib git_workflow.merge_to(target)
│   ├── push_canary, push_develop, push_auto, push_test       # push_to одиночный репо / автоматическое пакетно вне git
│   ├── switch_branch, sync_master, find_git_repos, git_fetch_all
│   ├── loop, unsleep, reindex
│   └── inject                    # внедрить bin/ в PATH оболочки
├── lib/
│   ├── commands/{домен}/{команда}.py    # Бизнес-логика каждой команды, экспортирует main(argv) -> int
│   │   ├── build/  file/  git/  process/  misc/  system/
│   │   └── git/merge.py + git/push.py также экспортирует run(target, argv)
│   └── {домен}.py                    # Общая библиотека (git/exec/ui/notify/build/process/...)
├── tests/                        # Набор unittest
├── commit / prc / issue          # Скрипты bash, для переписывания в py (временно хранятся в корне)
└── README.md
```

## Цепочка вызовов

```
bin/{скрипт}            (3 строки хака пути + import)
  → lib.commands.{домен}.{команда}.main(argv)
    → Общая библиотека lib/{домен}.py
```

Лёгкие входы только передают argv в бизнес-модуль, **не пишут бизнес-логику**. Общие возможности (операции git, выполнение команд, UI, уведомления, обнаружение сборки, управление процессами...) оседают в `lib/{домен}.py`, повторно используемые между командами.

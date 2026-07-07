# Скрипты

## Установка

```bash
./bin/inject            # сгенерировать ~/.scripts.sh и source во все rc
./bin/inject --show     # предпросмотр содержимого для записи
./bin/inject --uninstall  # удалить
```

inject идемпотентен : повторный запуск не добавит дубликаты. После перезапуска оболочки или `source ~/.zshrc` можно вызывать напрямую из любого каталога.

## Таблица функциональности

| Скрипт             | Функция                                                         | Пример                          |
| :--------------- | :----------------------------------------------------------- | :---------------------------- |
| `checkwork`      | Автоматизированная проверка компиляции + голосовые уведомления            | `checkwork`                   |
| `cpd`            | Глубокое копирование (по умолчанию только добавление/обновление ; `-f` удаляет лишнее)        | `cpd src/* dest/`             |
| `kk`             | Завершить процессы по имени                                              | `kk nginx`                    |
| `kkp`            | Завершить процессы по порту                                                | `kkp 8080`                    |
| `n`              | Голосовая трансляция macOS (`say`)                                       | `n "сборка завершена"`                |
| `loop`           | Выполнять команды в цикле, отслеживать успех/неудачу                                  | `loop 10 curl url`            |
| `merge_canary`   | Слить текущую ветку → canary, остаться в canary                           | `merge_canary [--dry-run]`     |
| `merge_develop`  | Слить текущую ветку → develop, остаться в develop                         | `merge_develop`                |
| `merge_auto`     | Слить текущую ветку → удалённая ветка по умолчанию, остаться в цели                        | `merge_auto`                   |
| `merge_test`     | Слить текущую ветку → test, остаться в test                               | `merge_test`                   |
| `push_canary`    | Слить текущую ветку → canary, отправить затем вернуться в исходную ветку                      | `push_canary [--stay]`         |
| `push_develop` / `push_auto` / `push_test` | Аналогично, цели develop / удалённая по умолчанию / test соответственно      |                               |
| `push_*` (пакеты)  | При выполнении push_* вне git каталога, автоматически пакетно : сканировать GitLab репозитории в подкаталогах и отправить по одному | `push_canary [--dry-run]` |
| `switch_branch`  | Переключить ветки пакетно (создаёт из origin/master если отсутствует)                 | `switch_branch <branch>`      |
| `sync_master`    | Синхронизировать master пакетно                                              | `sync_master`                 |
| `sync_branch`    | Пакетно синхронизировать текущую (или заданную) ветку с origin/<branch>                             | `sync_branch [branch] [--force]` |
| `fetch_all`  | Получить пакетно все Git репозитории                                     | `fetch_all`               |
| `unsleep`        | Избегать засыпания macOS caffeinate                                      | `unsleep -t 3600`             |
| `reindex`        | Переиндексировать проект (local-only, .gitignore)                        | `reindex`                     |
| `inject`         | Внедрить bin/ в PATH оболочки                                      | `inject`                      |

## Заметки о миграции (старые имена удалены)

- `mergec` / `mergedev` / `mergem` / `merget` → `merge_canary` / `merge_develop` / `merge_auto` / `merge_test`
- `pushc` / `pushdev` / `pushm` / `pusht` → `push_canary` / `push_develop` / `push_auto` / `push_test`
- `pushc_all` включён в `push_*` : выполнение вне git каталога автоматически активирует пакетный режим, автоматическое выполнение без подтверждения, `--dry-run` для предпросмотра.

## Переменные окружения

- `BATCH_CONCURRENCY` : верхний предел параллелизма для пакетных операций (`push_*` / `switch_branch` / `sync_branch` / `sync_master`), по умолчанию `4`. Пример : `BATCH_CONCURRENCY=8 push_canary`.

## Зависимости окружения

- **Python 3.10+** (лёгкий вход и основная логика)
- **Git** (merge_* / push_* / switch_branch / sync_master / fetch_all)
- **macOS** (`n` использует `say`, `unsleep` использует `caffeinate`)
- **rich** (украшение вывода, `pip install rich`)
- **pgrep / ps / lsof / kill** (kk / kkp)

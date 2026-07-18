# scripts

[简体中文](README.md) | [English](README.en.md) | [Français](README.fr.md) | [Español](README.es.md) | [Русский](README.ru.md) | [العربية](README.ar.md)

Коллекция утилит эффективности разработки — различные сокращения скриптов. Лёгкие входы Bash/Python, основная логика в `lib/`.

---

## Установка : Внедрить bin/ в PATH

```bash
./bin/inject            # Сгенерировать ~/.scripts.sh и source во все rc (~/.bashrc / ~/.zshrc / ~/.profile / ~/.bash_profile)
./bin/inject --show     # Предпросмотр содержимого для записи
./bin/inject --uninstall  # Удалить
```

inject идемпотентен : повторный запуск не дублирует. После завершения перезапустите оболочку или `source ~/.zshrc`, затем вызывайте `checkwork` / `merge_canary` / ... из любого каталога.

---

## Функциональность скриптов

| Скрипт             | Функция                                                         | Пример                          |
| :--------------- | :----------------------------------------------------------- | :---------------------------- |
| `checkwork`      | Автоматизированная проверка компиляции + голосовые уведомления            | `checkwork`                   |
| `cpd`            | Глубокое копирование (по умолчанию только добавление/обновление ; `-f` удаляет лишнее)        | `cpd src/* dest/`             |
| `kk`             | Завершить процессы по имени                                             | `kk nginx`                    |
| `kkp`            | Завершить процессы по порту                                                | `kkp 8080`                    |
| `n`              | Голосовая трансляция macOS (`say`)                                       | `n "сборка завершена"`                |
| `loop`           | Выполнять команды в цикле, отслеживать успех/неудачу                                  | `loop 10 curl url`            |
| `merge_canary`   | Слить текущую ветку → canary, остаться в canary                           | `merge_canary [--dry-run]`     |
| `merge_develop`  | Слить текущую ветку → develop, остаться в develop                         | `merge_develop`                |
| `merge_master`     | Слить текущую ветку → главная ветка (авто-определение master/main), остаться в цели                        | `merge_master`                   |
| `merge_test`     | Слить текущую ветку → test, остаться в test                               | `merge_test`                   |
| `push_canary`    | Слить текущую ветку → canary, отправить затем вернуться                      | `push_canary [--stay]`         |
| `push_develop` / `push_master` / `push_test` | Аналогично, цели develop / главная (авто-определение) / test соответственно      |                               |
| `push_*` (пакеты)  | При выполнении push_* вне git каталога, автоматически пакетно : сканировать Git репозитории в подкаталогах и отправить по одному | `push_canary [--dry-run]` |
| `switch_branch`  | Переключить ветки пакетно (создаёт из ветки по умолчанию (авто-определение) если отсутствует)                 | `switch_branch <branch>`      |
| `sync_master`    | Синхронизировать master пакетно = `sync_branch master`                                              | `sync_master`                 |
| `sync_branch`    | Пакетно синхронизировать текущую (или заданную) ветку с origin/<branch> | `sync_branch [branch] [--force]` |
| `delete_branch` | Удалить локальную ветку (один репо; пакетно если не в git dir) | `delete_branch <name> [--force] [-y]` |
| `delete_branch_remote` | Удалить удалённую ветку (один репо; пакетно если не в git dir) | `delete_branch_remote <name> [--remote <r>] [-y]` |
| `fetch_all`  | Получить пакетно все Git репозитории                                     | `fetch_all`               |
| `list_branch`  | Показать локальные ветки (один репо или сканировать все, дубликаты имён cross-repo отмечены ⟱; сгруппированы по репо) | `list_branch` |
| `unsleep`        | Избегать засыпания macOS caffeinate                                      | `unsleep -t 3600`             |
| `reindex`        | Переиндексировать проект (local-only, .gitignore)                        | `reindex`                     |
| `inject`         | Внедрить bin/ в PATH оболочки                                      | `inject`                      |

> **Заметки о миграции (старые имена удалены)** : `mergec`/`mergedev`/`mergem`/`merget` → `merge_canary`/`merge_develop`/`merge_master`/`merge_test` ; `pushc`/`pushdev`/`pushm`/`pusht` → `push_canary`/`push_develop`/`push_master`/`push_test` ; `pushc_all` включён в `push_*` (выполнить вне git каталога для авто пакета, авто выполнение без подтверждения, `--dry-run` предпросмотр).

> **Переменные окружения** : `BATCH_CONCURRENCY` контролирует операцию пакетами (`push_*` / `switch_branch` / `sync_branch` / `sync_master`) лимит параллелизма, по умолчанию `4`. Пример : `BATCH_CONCURRENCY=8 push_canary`.

---

## Документация

Полный сайт документации : https://lazygophers.github.io/scripts/

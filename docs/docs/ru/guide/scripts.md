# Скрипты

## Без установки: запуск прямо из GitHub

```bash
uvx git+https://github.com/lazygophers/scripts                     # список всех инструментов (то же, что lazyhelp)
uvx --from git+https://github.com/lazygophers/scripts checkwork    # запустить один
uv tool install git+https://github.com/lazygophers/scripts         # постоянная установка, команды остаются в PATH
```

`uvx` идёт вместе с [uv](https://docs.astral.sh/uv/): скачивает инструмент, запускает один раз и ничего не оставляет — клонировать репозиторий не нужно. Без имени команды запускается точка входа `scripts`, то есть `lazyhelp`; с `--from` uv обязательно требует имя команды.

## Установка

```bash
./bin/inject            # сгенерировать ~/.scripts.sh и source во все rc
./bin/inject show     # предпросмотр содержимого для записи
./bin/inject uninstall  # удалить
```

inject идемпотентен : повторный запуск не добавит дубликаты. После перезапуска оболочки или `source ~/.zshrc` можно вызывать напрямую из любого каталога.

## Таблица функциональности

Семь категорий по назначению. Индекс в терминале: `lazyhelp`; полное использование: `<инструмент> --help`; руководство для ИИ: `<инструмент> --skills`.

### Git-процессы

| Скрипт | Функция | Пример |
| :--- | :--- | :--- |
| `merge_canary` | Сливает текущую ветку → canary, остаётся на canary | `merge_canary [--dry-run]` |
| `merge_develop` / `merge_dev` / `merge_test` | То же, цели develop / dev / test | `merge_develop` |
| `merge_master` | Сливает текущую ветку → основную (master/main определяется автоматически), остаётся на цели | `merge_master` |
| `merge_branch` | Сливает текущую ветку → указанную (имя ветки — обязательный 1-й аргумент) | `merge_branch feature/x` |
| `push_canary` | Сливает текущую ветку → canary, пушит и возвращается | `push_canary [--stay]` |
| `push_develop` / `push_dev` / `push_test` | То же, цели develop / dev / test |  |
| `push_master` | То же, цель — автоматически определённая основная ветка |  |
| `push_branch` | Пушит текущую ветку в указанную (имя ветки — обязательный 1-й аргумент) | `push_branch feature/x` |
| `switch_branch` | Пакетное переключение веток (создаёт от основной, если нет) | `switch_branch <branch>` |
| `sync_branch` | Пакетная синхронизация текущей (или указанной) ветки с origin/<branch> | `sync_branch [branch] [--force]` |
| `sync_master` | Пакетная синхронизация основной ветки (автоопределение) | `sync_master` |
| `delete_branch` | Удаляет локальную ветку (один репо; пакетно вне git) | `delete_branch <name> [--force] [-y]` |
| `delete_branch_remote` | Удаляет удалённую ветку (один репо; пакетно вне git) | `delete_branch_remote <name> [--remote <r>] [-y]` |

> Запуск этих команд вне git-каталога включает пакетный режим: сканирует подкаталоги git и выполняет по очереди.

### Git-сотрудничество

| Скрипт | Функция | Пример |
| :--- | :--- | :--- |
| `commit` | Автокоммит (claude генерирует сообщение) | `commit` |
| `mr` | Автосоздание PR/MR (claude генерирует заголовок/тело, по умолчанию draft) | `mr [base]` |
| `issue` | Автосоздание Issue (claude генерирует заголовок/тело) | `issue` |
| `squash_pr` | Сжимает текущую ветку в один коммит → открывает PR через mr (2-й аргумент — имя PR-ветки, по умолчанию `<текущая>_pr`) | `squash_pr <target> [pr_branch]` |
| `fetch_all` | Пакетный fetch всех git-репозиториев | `fetch_all` |
| `list_branch` | Список локальных веток (один репо или сканирование всех, дубли ⟱) | `list_branch` |

### Сборка и проверки

| Скрипт | Функция | Пример |
| :--- | :--- | :--- |
| `checkwork` | Многоязычный гейт сборки перед push (Go/Rust/Python/Java/Node) + голосовое уведомление | `checkwork` |
| `check_ai` | Проверка доступности AI API эндпоинтов (пустой POST) | `check_ai` |
| `cicd` | Опрашивает CI/CD текущей ветки, выводит итоговый результат | `cicd` |

### Данные и сеть

| Скрипт | Функция | Пример |
| :--- | :--- | :--- |
| `archery` | CLI платформы Archery SQL (запросы / workflow, вход по домену) | `archery query execute 'select 1' --instance-name prod --db-name orders` |
| `grafana` | CLI для HTTP API Grafana (вход по домену) | `grafana health` |
| `email` | Почта для нескольких ящиков: приём и отправка (QQ/Gmail/163/126/iCloud/Fastmail/Zoho, вход по адресу) | `email inbox` |
| `ovpn` | Клиент OpenVPN (автозаполнение учётных данных и TOTP, split tunneling) | `ovpn connect` |
| `vpn-prio` | Меняет приоритет сетевых сервисов macOS (понижает default-маршрут OpenVPN) | `vpn-prio --help` |
| `ipinfo` | Локальный IP + тип сети (детект хотспота) | `ipinfo` |
| `disable-ipv6` / `enable-ipv6` | Отключает/включает IPv6 на всех сетевых сервисах (нужен sudo) | `sudo disable-ipv6` |

### Веб-поиск

| Скрипт | Функция | Пример |
| :--- | :--- | :--- |
| `websearch` | Мультидвижковый веб-поиск (без ключей, параллельно, дедуп по URL) | `websearch rust async` |
| `webgrab` | Страница → Markdown (обход антибота + рендер Playwright + 34 сайта + сохранённый вход) | `webgrab https://example.com` |

### Процессы и запуск

| Скрипт | Функция | Пример |
| :--- | :--- | :--- |
| `kk` | Завершает процессы по имени | `kk nginx` |
| `kkp` | Завершает процессы по порту | `kkp 8080` |
| `loop` | Циклический запуск команды, отслеживание успеха/ошибок | `loop 10 curl url` |
| `unsleep` | Антисон macOS (caffeinate) | `unsleep timed 2h` |

### Файлы и система

| Скрипт | Функция | Пример |
| :--- | :--- | :--- |
| `cpd` | Глубокое копирование (по умолчанию добавление/обновление; `-f` удаляет лишнее) | `cpd src/* dest/` |
| `n` | Голосовое оповещение macOS (`say`) | `n "build complete"` |
| `inject` | Внедряет bin/ в PATH оболочки | `inject` |
| `graphwatch` | Демон graphify: автоперестройка графа знаний | `graphwatch add <dir>` |
| `lazyhelp` | Индекс инструментов в терминале + проброс `--help` | `lazyhelp help <tool>` |

## Заметки о миграции (старые имена удалены)

- `mergec` / `mergedev` / `mergem` / `merget` → `merge_canary` / `merge_develop` / `merge_master` / `merge_test`
- `pushc` / `pushdev` / `pushm` / `pusht` → `push_canary` / `push_develop` / `push_master` / `push_test`
- `pushc_all` включён в `push_*` : выполнение вне git каталога автоматически активирует пакетный режим, автоматическое выполнение без подтверждения, `--dry-run` для предпросмотра.

## Переменные окружения

- `BATCH_CONCURRENCY` : верхний предел параллелизма для пакетных операций (`push_*` / `switch_branch` / `sync_branch` / `sync_master`), по умолчанию `4`. Пример : `BATCH_CONCURRENCY=8 push_canary`.

## Зависимости окружения

- **Python 3.10+** (лёгкий вход и основная логика)
- **Git** (merge_* / push_* / switch_branch / sync_master / fetch_all / delete_branch)
- **macOS** (`n` использует `say`, `unsleep` использует `caffeinate`)
- **rich** (украшение вывода, `pip install rich`)
- **pgrep / ps / lsof / kill** (kk / kkp)

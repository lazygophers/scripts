# Scripts

## Instalación

```bash
./bin/inject            # generar ~/.scripts.sh y source a todos los rc
./bin/inject --show     # previsualizar el contenido a escribir
./bin/inject --uninstall  # desinstalar
```

inject es idempotente : reejecutar no añadirá duplicados. Después de reiniciar el shell o `source ~/.zshrc`, puede llamar directamente desde cualquier directorio.

## Tabla de funcionalidades

| Script             | Función                                                         | Ejemplo                          |
| :--------------- | :----------------------------------------------------------- | :---------------------------- |
| `checkwork`      | Verificación automatizada de compilación + notificaciones de voz            | `checkwork`                   |
| `cpd`            | Copia profunda (por defecto solo añadir/actualizar ; `-f` elimina excesivos)        | `cpd src/* dest/`             |
| `kk`             | Terminar procesos por nombre                                              | `kk nginx`                    |
| `kkp`            | Terminar procesos por puerto                                                | `kkp 8080`                    |
| `n`              | Difusión de voz macOS (`say`)                                       | `n "construcción terminada"`                |
| `loop`           | Ejecutar comandos en bucle, seguir éxito/fracaso                                  | `loop 10 curl url`            |
| `merge_canary`   | Fusionar rama actual → canary, quedarse en canary                           | `merge_canary [--dry-run]`     |
| `merge_develop`  | Fusionar rama actual → develop, quedarse en develop                         | `merge_develop`                |
| `merge_auto`     | Fusionar rama actual → rama por defecto remota, quedarse en objetivo                        | `merge_auto`                   |
| `merge_test`     | Fusionar rama actual → test, quedarse en test                               | `merge_test`                   |
| `push_canary`    | Fusionar rama actual → canary, empujar luego volver a rama original                      | `push_canary [--stay]`         |
| `push_develop` / `push_auto` / `push_test` | Ídem, objetivos develop / remoto por defecto / test respectivamente      |                               |
| `push_*` (lotes)  | Al ejecutar push_* en directorio no-git, automáticamente por lotes : escanear repositorios GitLab en subdirectorios y empujar uno por uno | `push_canary [--dry-run]` |
| `switch_branch`  | Cambiar ramas por lotes (crea desde origin/master si inexistente)                 | `switch_branch <branch>`      |
| `sync_master`    | Sincronizar master por lotes                                              | `sync_master`                 |
| `sync_branch`    | Sincronizar por lotes rama actual (o dada) a origin/<branch>                             | `sync_branch [branch] [--force]` |
| `fetch_all`  | Recuperar por lotes todos los repositorios Git                                     | `fetch_all`               |
| `unsleep`        | Evitar suspensión macOS caffeinate                                      | `unsleep -t 3600`             |
| `reindex`        | Reindexar proyecto (local-only, .gitignore)                        | `reindex`                     |
| `inject`         | Inyectar bin/ en PATH shell                                      | `inject`                      |

## Notas de migración (nombres antiguos eliminados)

- `mergec` / `mergedev` / `mergem` / `merget` → `merge_canary` / `merge_develop` / `merge_auto` / `merge_test`
- `pushc` / `pushdev` / `pushm` / `pusht` → `push_canary` / `push_develop` / `push_auto` / `push_test`
- `pushc_all` se fusionó en `push_*` : ejecutar en directorio no-git activa automáticamente modo por lotes, ejecución automática sin confirmación, `--dry-run` para previsualizar.

## Variables de entorno

- `BATCH_CONCURRENCY` : límite superior de paralelismo para operaciones por lotes (`push_*` / `switch_branch` / `sync_branch` / `sync_master`), por defecto `4`. Ejemplo : `BATCH_CONCURRENCY=8 push_canary`.

## Dependencias de entorno

- **Python 3.10+** (entrada ligera y lógica principal)
- **Git** (merge_* / push_* / switch_branch / sync_master / fetch_all)
- **macOS** (`n` usa `say`, `unsleep` usa `caffeinate`)
- **rich** (embellecimiento de salida, `pip install rich`)
- **pgrep / ps / lsof / kill** (kk / kkp)

# scripts

[简体中文](README.md) | [English](README.en.md) | [Français](README.fr.md) | [Español](README.es.md) | [Русский](README.ru.md) | [العربية](README.ar.md)

Colección de utilidades de eficiencia de desarrollo — varios accesos directos de scripts. Entradas ligeras Bash/Python, lógica principal en `lib/`.

---

## Instalación : Inyectar bin/ en PATH

```bash
./bin/inject            # Generar ~/.scripts.sh y source a todos los rc (~/.bashrc / ~/.zshrc / ~/.profile / ~/.bash_profile)
./bin/inject --show     # Previsualizar el contenido a escribir
./bin/inject --uninstall  # Desinstalar
```

inject es idempotente : reejecutar no duplicará. Después de completar, reinicie shell o `source ~/.zshrc`, luego llame a `checkwork` / `merge_canary` / ... desde cualquier directorio.

---

## Funcionalidades de scripts

| Script             | Función                                                         | Ejemplo                          |
| :--------------- | :----------------------------------------------------------- | :---------------------------- |
| `checkwork`      | Verificación automática de compilación + notificaciones de voz            | `checkwork`                   |
| `cpd`            | Copia profunda (por defecto solo añadir/actualizar ; `-f` eliminar excesivos)        | `cpd src/* dest/`             |
| `kk`             | Terminar procesos por nombre                                             | `kk nginx`                    |
| `kkp`            | Terminar procesos por puerto                                                | `kkp 8080`                    |
| `n`              | Difusión de voz macOS (`say`)                                       | `n "construcción terminada"`                |
| `loop`           | Ejecutar comandos en bucle, seguir éxito/fracaso                                  | `loop 10 curl url`            |
| `merge_canary`   | Fusionar rama actual → canary, quedarse en canary                           | `merge_canary [--dry-run]`     |
| `merge_develop`  | Fusionar rama actual → develop, quedarse en develop                         | `merge_develop`                |
| `merge_master`     | Fusionar rama actual → rama principal (auto-detect master/main), quedarse en objetivo                        | `merge_master`                   |
| `merge_test`     | Fusionar rama actual → test, quedarse en test                               | `merge_test`                   |
| `push_canary`    | Fusionar rama actual → canary, empujar luego volver                      | `push_canary [--stay]`         |
| `push_develop` / `push_master` / `push_test` | Ídem, objetivos develop / rama principal (auto-detect) / test respectivamente      |                               |
| `push_*` (lotes)  | Al ejecutar push_* en directorio no-git, automáticamente por lotes : escanear repositorios Git en subdirectorios y empujar uno por uno | `push_canary [--dry-run]` |
| `switch_branch`  | Cambiar ramas por lotes (crea desde rama por defecto (auto-detectada) si inexistente)                 | `switch_branch <branch>`      |
| `sync_master`    | Sincronizar master por lotes = `sync_branch master`                                              | `sync_master`                 |
| `sync_branch`    | Sincronizar por lotes rama actual (o dada) a origin/<branch> | `sync_branch [branch] [--force]` |
| `delete_branch` | Eliminar rama local (repo único; lote si no en dir git) | `delete_branch <name> [--force] [-y]` |
| `delete_branch_remote` | Eliminar rama remota (repo único; lote si no en dir git) | `delete_branch_remote <name> [--remote <r>] [-y]` |
| `fetch_all`  | Recuperar por lotes todos los repositorios Git                                     | `fetch_all`               |
| `list_branch`  | Listar ramas locales (repo único o escanear todos, nombres duplicados cross-repo marcados ⟱ ; agrupado por repo) | `list_branch` |
| `unsleep`        | Evitar suspensión macOS caffeinate                                      | `unsleep -t 3600`             |
| `reindex`        | Reindexar proyecto (local-only, .gitignore)                        | `reindex`                     |
| `inject`         | Inyectar bin/ en PATH shell                                      | `inject`                      |

> **Notas de migración (nombres antiguos eliminados)** : `mergec`/`mergedev`/`mergem`/`merget` → `merge_canary`/`merge_develop`/`merge_master`/`merge_test` ; `pushc`/`pushdev`/`pushm`/`pusht` → `push_canary`/`push_develop`/`push_master`/`push_test` ; `pushc_all` se fusionó en `push_*` (ejecutar en directorio no-git para auto batch, ejecución auto sin confirmación, `--dry-run` previsualizar).

> **Variables de entorno** : `BATCH_CONCURRENCY` controla la operación por lotes (`push_*` / `switch_branch` / `sync_branch` / `sync_master`) límite de concurrencia paralela, por defecto `4`. Ejemplo : `BATCH_CONCURRENCY=8 push_canary`.
>
> **Opción global `--no-say`** : todos los `bin/*` (salvo el propio `n`) admiten `--no-say` para silenciar la voz de macOS ; equivalente a `SCRIPTS_NO_SAY=1`. Ejemplo : `delete_branch --no-say hotfix/x`, `push_canary --no-say`.
>
> **Opción `push_*` `--no-check`** : omite las puertas de checkwork (pre-verificación rama actual + verificación del resultado fusionado) ; el resto del flujo no cambia. Ejemplo : `push_canary --no-check`.

---

## Documentación

Sitio completo de documentación : https://lazygophers.github.io/scripts/

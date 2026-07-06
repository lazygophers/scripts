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
| `merge_auto`     | Fusionar rama actual → rama por defecto remota, quedarse en objetivo                        | `merge_auto`                   |
| `merge_test`     | Fusionar rama actual → test, quedarse en test                               | `merge_test`                   |
| `push_canary`    | Fusionar rama actual → canary, empujar luego volver                      | `push_canary [--stay]`         |
| `push_develop` / `push_auto` / `push_test` | Ídem, objetivos develop / remoto por defecto / test respectivamente      |                               |
| `push_*` (lotes)  | Al ejecutar push_* en directorio no-git, automáticamente por lotes : escanear repositorios GitLab en subdirectorios y empujar uno por uno | `push_canary [--dry-run]` |
| `switch_branch`  | Cambiar ramas por lotes (crea desde origin/master si inexistente)                 | `switch_branch <branch>`      |
| `sync_master`    | Sincronizar master por lotes                                              | `sync_master`                 |
| `find_git_repos` | Listar todos los repositorios Git bajo directorio                                      | `find_git_repos`              |
| `git_fetch_all`  | Recuperar por lotes todos los repositorios Git                                     | `git_fetch_all`               |
| `unsleep`        | Evitar suspensión macOS caffeinate                                      | `unsleep -t 3600`             |
| `reindex`        | Reindexar proyecto (local-only, .gitignore)                        | `reindex`                     |
| `inject`         | Inyectar bin/ en PATH shell                                      | `inject`                      |

> **Notas de migración (nombres antiguos eliminados)** : `mergec`/`mergedev`/`mergem`/`merget` → `merge_canary`/`merge_develop`/`merge_auto`/`merge_test` ; `pushc`/`pushdev`/`pushm`/`pusht` → `push_canary`/`push_develop`/`push_auto`/`push_test` ; `pushc_all` se fusionó en `push_*` (ejecutar en directorio no-git para auto batch, ejecución auto sin confirmación, `--dry-run` previsualizar).

> **Variables de entorno** : `BATCH_CONCURRENCY` controla la operación por lotes (`push_*` / `switch_branch` / `sync_master`) límite de concurrencia paralela, por defecto `4`. Ejemplo : `BATCH_CONCURRENCY=8 push_canary`.

---

## Documentación

Sitio completo de documentación : https://lazygophers.github.io/scripts/

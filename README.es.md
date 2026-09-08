# scripts

[简体中文](README.md) | [English](README.en.md) | [Français](README.fr.md) | [Español](README.es.md) | [Русский](README.ru.md) | [العربية](README.ar.md)

Colección de utilidades de eficiencia de desarrollo — varios accesos directos de scripts. `bin/` solo contiene entradas ligeras; las implementaciones están en `lib/cli/` y las capacidades compartidas en `lib/`.

---

## Sin instalación: ejecutar directamente desde GitHub

```bash
uvx git+https://github.com/lazygophers/scripts                     # listar todas las herramientas (igual que lazyhelp)
uvx --from git+https://github.com/lazygophers/scripts checkwork    # ejecutar una sola
```

`uvx` viene con [uv](https://docs.astral.sh/uv/): descarga una herramienta, la ejecuta una vez y no deja nada — sin clonar el repositorio. Sin nombre de comando ejecuta el punto de entrada `scripts`, que es `lazyhelp`. Con `--from`, uv exige un nombre de comando explícito.

Para conservarlas, instala una vez (`uv tool install` es una instalación permanente; los comandos quedan en el PATH):

```bash
uv tool install git+https://github.com/lazygophers/scripts
```

---

## Instalación : Inyectar bin/ en PATH

```bash
./bin/inject            # Generar ~/.scripts.sh y source a todos los rc (~/.bashrc / ~/.zshrc / ~/.profile / ~/.bash_profile)
./bin/inject show     # Previsualizar el contenido a escribir
./bin/inject uninstall  # Desinstalar
```

inject es idempotente : reejecutar no duplicará. Después de completar, reinicie shell o `source ~/.zshrc`, luego llame a `checkwork` / `merge_canary` / ... desde cualquier directorio.

---

## Funcionalidades de scripts

Siete categorías por uso. Índice en terminal: `lazyhelp`; uso completo: `<herramienta> --help`; guía para IA: `<herramienta> --skills`.

### Flujo Git

| Script | Función | Ejemplo |
| :--- | :--- | :--- |
| `merge_canary` | Fusiona la rama actual → canary, permanece en canary | `merge_canary [--dry-run]` |
| `merge_develop` / `merge_dev` / `merge_test` | Igual, objetivos develop / dev / test | `merge_develop` |
| `merge_master` | Fusiona la rama actual → rama principal (master/main detectado), permanece en el objetivo | `merge_master` |
| `merge_branch` | Fusiona la rama actual → rama indicada (nombre obligatorio como 1er argumento) | `merge_branch feature/x` |
| `push_canary` | Fusiona la rama actual → canary, empuja y vuelve | `push_canary [--stay]` |
| `push_develop` / `push_dev` / `push_test` | Igual, objetivos develop / dev / test |  |
| `push_master` | Igual, objetivo la rama principal detectada |  |
| `push_branch` | Empuja la rama actual a la rama indicada (nombre obligatorio como 1er argumento) | `push_branch feature/x` |
| `switch_branch` | Cambio de rama por lotes (crea desde la principal si no existe) | `switch_branch <branch>` |
| `sync_branch` | Sincroniza por lotes la rama actual (o dada) con origin/<branch> | `sync_branch [branch] [--force]` |
| `sync_master` | Sincroniza por lotes la rama principal (detectada) | `sync_master` |
| `delete_branch` | Borra rama local (un repo; lote fuera de git) | `delete_branch <name> [--force] [-y]` |
| `delete_branch_remote` | Borra rama remota (un repo; lote fuera de git) | `delete_branch_remote <name> [--remote <r>] [-y]` |

> Ejecutados fuera de un repo git, estos comandos pasan a modo lote: escanean subdirectorios git y ejecutan uno por uno.

### Colaboración Git

| Script | Función | Ejemplo |
| :--- | :--- | :--- |
| `commit` | Commit automático (claude genera el mensaje) | `commit` |
| `mr` | Crea PR/MR automático (claude genera título/cuerpo, draft por defecto) | `mr [base]` |
| `issue` | Crea Issue automático (claude genera título/cuerpo) | `issue` |
| `squash_pr` | Comprime source en un commit → abre PR vía mr | `squash_pr [source] <target>` |
| `fetch_all` | Fetch por lotes de todos los repos Git | `fetch_all` |
| `list_branch` | Lista ramas locales (un repo o escaneo global, duplicados ⟱) | `list_branch` |

### Build y Verificación

| Script | Función | Ejemplo |
| :--- | :--- | :--- |
| `checkwork` | Compuerta de build multi-lenguaje pre-push (Go/Rust/Python/Java/Node) + aviso de voz | `checkwork` |
| `check_ai` | Test de conectividad de endpoints de API de IA (POST vacío) | `check_ai` |
| `cicd` | Sondea el CI/CD de la rama actual, muestra el resultado final | `cicd` |

### Datos y Red

| Script | Función | Ejemplo |
| :--- | :--- | :--- |
| `archery` | CLI de Archery SQL (consultas / workflow, login por dominio) | `archery query execute 'select 1' --instance-name prod --db-name orders` |
| `grafana` | CLI de la API HTTP de Grafana (login por dominio) | `grafana health` |
| `ovpn` | Cliente OpenVPN (credenciales y TOTP automáticos, split tunneling) | `ovpn connect` |
| `vpn-prio` | Ajusta la prioridad de servicios de red de macOS (baja la ruta OpenVPN) | `vpn-prio --help` |
| `ipinfo` | IP LAN + tipo de red (detección de hotspot) | `ipinfo` |
| `disable-ipv6` / `enable-ipv6` | Desactiva/activa IPv6 en todos los servicios de red (requiere sudo) | `sudo disable-ipv6` |

### Búsqueda Web

| Script | Función | Ejemplo |
| :--- | :--- | :--- |
| `websearch` | Búsqueda web multi-motor (sin clave, paralela, deduplicada por URL) | `websearch rust async` |
| `webgrab` | Página web → Markdown (anti-bot + render Playwright + 34 sitios + login persistente) | `webgrab https://example.com` |

### Procesos y Ejecución

| Script | Función | Ejemplo |
| :--- | :--- | :--- |
| `kk` | Termina procesos por nombre | `kk nginx` |
| `kkp` | Termina procesos por puerto | `kkp 8080` |
| `loop` | Ejecuta un comando en bucle, registra éxito/fallo | `loop 10 curl url` |
| `unsleep` | Anti-suspensión macOS (caffeinate) | `unsleep timed 2h` |

### Archivos y Sistema

| Script | Función | Ejemplo |
| :--- | :--- | :--- |
| `cpd` | Copia profunda (por defecto añade/actualiza; `-f` borra extras) | `cpd src/* dest/` |
| `n` | Locución de voz de macOS (`say`) | `n "build complete"` |
| `inject` | Inyecta bin/ en el PATH del shell | `inject` |
| `graphwatch` | Demonio graphify: reconstruye el grafo de conocimiento automáticamente | `graphwatch add <dir>` |
| `lazyhelp` | Índice de herramientas en terminal + reenvío de `--help` | `lazyhelp help <tool>` |

> **Notas de migración (nombres antiguos eliminados)** : `mergec`/`mergedev`/`mergem`/`merget` → `merge_canary`/`merge_develop`/`merge_master`/`merge_test` ; `pushc`/`pushdev`/`pushm`/`pusht` → `push_canary`/`push_develop`/`push_master`/`push_test` ; `pushc_all` se fusionó en `push_*` (ejecutar en directorio no-git para auto batch, ejecución auto sin confirmación, `--dry-run` previsualizar).

> **Variables de entorno** : `BATCH_CONCURRENCY` controla la operación por lotes (`push_*` / `switch_branch` / `sync_branch` / `sync_master`) límite de concurrencia paralela, por defecto `4`. Ejemplo : `BATCH_CONCURRENCY=8 push_canary`.
>
> **Opción global `--no-say`** : todos los `bin/*` (salvo el propio `n`) admiten `--no-say` para silenciar la voz de macOS ; equivalente a `SCRIPTS_NO_SAY=1`. Ejemplo : `delete_branch --no-say hotfix/x`, `push_canary --no-say`.
>
> **Opción `push_*` `--no-check`** : omite las puertas de checkwork (pre-verificación rama actual + verificación del resultado fusionado) ; el resto del flujo no cambia. Ejemplo : `push_canary --no-check`.

---

## Documentación

Sitio completo de documentación : https://lazygophers.github.io/scripts/

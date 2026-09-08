# Introducción

`scripts` es una colección de utilidades de eficiencia — atajos para tareas comunes de desarrollo y operaciones. `bin/` solo contiene entradas finas; las implementaciones están en `lib/cli/` y las capacidades compartidas en `lib/`.

## Puntos destacados

- **Entradas finas**: cada script de `bin/` es la misma cáscara (ajuste de path + `from lib.cli.<módulo> import main`) — sin lógica de negocio y sin symlinks; la implementación es `lib/cli/<nombre>.py` y las capacidades compartidas son módulos planos de `lib/`.
- **Ejecución remota sin instalar**: todos los comandos están registrados en `[project.scripts]`, así que `uvx git+https://github.com/lazygophers/scripts` funciona sin clonar.
- **Clasificación por uso**: Flujo Git / Colaboración Git / Build y Verificación / Datos y Red / Búsqueda Web / Procesos y Ejecución / Archivos y Sistema — una página con `lazyhelp`.
- **Operaciones por lotes**: `merge_*` / `push_*` / `switch_branch` / `sync_master` cubren un repo y lotes multi-repo.
- **Seguridad primero**: autoexclusión en gestión de procesos, verificación de limpieza y rollback antes de operaciones Git.

## Inicio rápido

Ejecutar una vez, sin instalar nada:

```bash
uvx git+https://github.com/lazygophers/scripts                     # listar todas las herramientas
uvx --from git+https://github.com/lazygophers/scripts checkwork    # ejecutar una sola
```

Mantenerlas en esta máquina:

```bash
./bin/inject            # inyecta el bin/ clonado en el PATH del shell
```

Reinicia el shell después y llama a `checkwork` / `merge_canary` / ... desde cualquier directorio.

Ver [Scripts](./scripts.md) y el repo de GitHub [lazygophers/scripts](https://github.com/lazygophers/scripts).

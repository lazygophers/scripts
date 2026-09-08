# Introducción

`scripts` es una colección de utilidades de eficiencia — atajos para tareas comunes de desarrollo y operaciones. Entradas finas Bash/Python, lógica central en `lib/`.

## Puntos destacados

- **Entradas finas**: los scripts de `bin/` son 3 líneas de hack de path + import; toda la lógica vive en módulos planos de `lib/`.
- **Clasificación por uso**: Flujo Git / Colaboración Git / Build y Verificación / Datos y Red / Búsqueda Web / Procesos y Ejecución / Archivos y Sistema — una página con `lazyhelp`.
- **Operaciones por lotes**: `merge_*` / `push_*` / `switch_branch` / `sync_master` cubren un repo y lotes multi-repo.
- **Seguridad primero**: autoexclusión en gestión de procesos, verificación de limpieza y rollback antes de operaciones Git.

## Inicio rápido

```bash
./bin/inject            # inyecta bin/ en el PATH del shell
```

Reinicia el shell después y llama a `checkwork` / `merge_canary` / ... desde cualquier directorio.

Ver [Scripts](./scripts.md) y el repo de GitHub [lazygophers/scripts](https://github.com/lazygophers/scripts).

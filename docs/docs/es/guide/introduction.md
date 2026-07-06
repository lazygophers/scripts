# Introducción

`scripts` es una colección de utilidades de eficiencia de desarrollo — accesos rápidos para tareas comunes de desarrollo y operaciones. Entradas ligeras Bash/Python, con lógica principal establecida en `lib/`.

## Puntos destacados

- **Entradas ligeras** : los scripts en `bin/` son solo 3 líneas de hack de ruta + import ; toda la lógica de negocio vive en `lib/commands/`.
- **Clasificados por dominio** : `build` / `file` / `git` / `process` / `misc` / `system`, mutuamente aislados.
- **Operaciones por lotes** : `merge_*` / `push_*` / `switch_branch` / `sync_master` cubren tanto repositorio único como lotes multi-repositorio.
- **Seguridad primero** : autoexclusión de la gestión de procesos, verificaciones de limpieza del árbol de trabajo y reversión antes de operaciones Git.

## Inicio rápido

```bash
./bin/inject            # inyectar bin/ en el PATH del shell
```

Reinicie su shell después, luego llame a `checkwork` / `merge_canary` / ... desde cualquier directorio.

Vea [Scripts](./scripts.md) y el repositorio GitHub [lazygophers/scripts](https://github.com/lazygophers/scripts).

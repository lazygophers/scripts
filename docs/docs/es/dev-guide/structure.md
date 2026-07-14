# Estructura de directorios

```
scripts/
├── bin/                          # Scripts de entrada ligera (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_canary, merge_develop, merge_auto, merge_test   # llama lib git_workflow.merge_to(target)
│   ├── push_canary, push_develop, push_auto, push_test       # push_to repo único / automático por lotes fuera git
│   ├── switch_branch, sync_master, sync_branch, fetch_all, delete_branch, delete_branch_remote
│   ├── loop, unsleep, reindex
│   └── inject                    # inyectar bin/ en PATH shell
├── lib/
│   ├── commands/{dominio}/{comando}.py    # Lógica de negocio de cada comando, expone main(argv) -> int
│   │   ├── build/  file/  git/  process/  misc/  system/
│   │   └── git/merge.py + git/push.py también expone run(target, argv)
│   └── {dominio}.py                    # Biblioteca compartida (git/exec/ui/notify/build/process/...)
├── tests/                        # Suite unittest
├── commit / mr / issue          # Scripts bash, para reescribir en py (guardados temporalmente en raíz)
└── README.md
```

## Cadena de llamadas

```
bin/{script}            (3 líneas de hack de ruta + import)
  → lib.commands.{dominio}.{comando}.main(argv)
    → Biblioteca compartida lib/{dominio}.py
```

Las entradas ligeras solo transmiten argv al módulo de negocio, **no escriben lógica de negocio**. Las capacidades compartidas (operaciones git, ejecución de comandos, UI, notificaciones, detección de construcción, gestión de procesos...) se sedimentan en `lib/{dominio}.py`, reutilizables entre comandos.

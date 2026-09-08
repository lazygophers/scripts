# Estructura

```
scripts/
├── bin/                          # scripts de entrada finos (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_* / push_*          # todos symlinks → bin/_gitwf, despacho según el nombre
│   ├── switch_branch, sync_master, sync_branch, fetch_all, delete_branch, delete_branch_remote
│   ├── loop, unsleep, websearch, webgrab, archery, grafana, ovpn, ...
│   └── inject                    # inyecta bin/ en el PATH del shell
├── lib/                          # toda la lógica (plana, sin subdirectorios)
│   ├── {nombre}.py               # módulo de negocio por comando (git_workflow / batch_git / build / ...)
│   ├── fire_base.py              # BaseCli + run_cli + timed_cli, esqueleto común
│   ├── lazyhelp.py               # registro de herramientas (TOOLS = nombre → categoría + descripción)
│   ├── skills_help.py            # guía --skills para IA (COMMAND_SKILLS)
│   └── ui / notify / exec / process   # librerías compartidas
├── skills/lazyscripts/           # índice de skill IA (SKILL.md + archivos por tema)
├── docs/                         # sitio de docs Rspress (seis idiomas en docs/docs/<lang>/)
├── tests/                        # suite unittest
└── README.md (+ 5 traducciones)
```

## Cadena de llamada

```
bin/{script}            (3 líneas de path hack + import)
  → run_cli(<Nombre>Cli())      # lib/fire_base.py, despacho de subcomandos fire
    → función de negocio en lib/{nombre}.py
      → lib/ui.py / lib/exec.py / ... compartidas
```

La entrada fina solo entrega argv a `run_cli` — **sin lógica de negocio aquí**. `merge_*` / `push_*` son symlinks a `bin/_gitwf`; el basename de argv[0] decide la acción y la rama objetivo. Las capacidades compartidas viven en `lib/{dominio}.py`.

Toda herramienta pública nueva debe registrarse en `TOOLS` de `lib/lazyhelp.py`; la guía IA va en `COMMAND_SKILLS` de `lib/skills_help.py`.

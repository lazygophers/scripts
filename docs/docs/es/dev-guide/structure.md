# Estructura

```
scripts/
├── bin/                          # scripts de entrada finos (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_* / push_*          # 12 archivos reales, cada uno llama su entrada en lib/cli/gitwf.py
│   ├── switch_branch, sync_master, sync_branch, fetch_all, delete_branch, delete_branch_remote
│   ├── loop, unsleep, websearch, webgrab, archery, grafana, ovpn, ...
│   └── inject                    # inyecta bin/ en el PATH del shell
├── lib/                          # toda la lógica
│   ├── cli/{nombre}.py           # la CLI de un comando (lo que antes estaba en bin/)
│   ├── cli/gitwf.py              # implementación común de merge_*/push_* + 12 funciones de entrada
│   ├── cli/ipv6.py               # disable-ipv6 / enable-ipv6 (Python, ya no bash)
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
bin/{script}                       # repo clonado: cáscara de 3 líneas
  o el comando `{script}` instalado    # uvx / uv tool install: [project.scripts]
    → main() de lib/cli/{script}.py
      → run_cli(<Nombre>Cli())       # lib/fire_base.py, despacho de subcomandos fire
        → función de negocio en lib/{nombre}.py
          → lib/ui.py / lib/exec.py / ... compartidas
```

`bin/` **no tiene lógica de negocio ni symlinks**: cada shell es `from lib.cli.<módulo> import <fn> as main` + `raise SystemExit(main())`. La implementación vive en `lib/cli/<nombre>.py` (un módulo por comando) y llama a los ayudantes compartidos de `lib/{dominio}.py`. `merge_*` / `push_*` son 12 shells sobre `lib/cli/gitwf.py`, cada uno pasa `(name, action, target)` de forma explícita. Las mismas funciones están registradas como `[project.scripts]`, así que `uvx --from git+https://github.com/lazygophers/scripts <nombre>` ejecuta cualquier herramienta sin clonar. Ejecutar `uvx git+https://github.com/lazygophers/scripts` sin nombre de comando usa el punto de entrada `scripts`, un alias de `lazyhelp`.

Toda herramienta pública nueva debe registrarse en `TOOLS` de `lib/lazyhelp.py`; la guía IA va en `COMMAND_SKILLS` de `lib/skills_help.py`.

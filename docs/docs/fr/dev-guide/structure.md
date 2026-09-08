# Structure

```
scripts/
├── bin/                          # scripts d'entrée fins (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_* / push_*          # 12 vrais fichiers, chacun appelle son entrée dans lib/cli/gitwf.py
│   ├── switch_branch, sync_master, sync_branch, fetch_all, delete_branch, delete_branch_remote
│   ├── loop, unsleep, websearch, webgrab, archery, grafana, ovpn, ...
│   └── inject                    # injecte bin/ dans le PATH du shell
├── lib/                          # toute la logique
│   ├── cli/{nom}.py              # la CLI d'une commande (ce qui était dans bin/)
│   ├── cli/gitwf.py              # implémentation commune merge_*/push_* + 12 fonctions d'entrée
│   ├── cli/ipv6.py               # disable-ipv6 / enable-ipv6 (Python, plus du bash)
│   ├── {nom}.py                  # module métier par commande (git_workflow / batch_git / build / ...)
│   ├── fire_base.py              # BaseCli + run_cli + timed_cli, squelette commun des entrées
│   ├── lazyhelp.py               # registre d'outils (TOOLS = nom → catégorie + description)
│   ├── skills_help.py            # guidance --skills pour l'IA (COMMAND_SKILLS)
│   └── ui / notify / exec / process   # bibliothèques partagées
├── skills/lazyscripts/           # index de skill IA (SKILL.md + fichiers par sujet)
├── docs/                         # site de doc Rspress (sources six langues dans docs/docs/<lang>/)
├── tests/                        # suite unittest
└── README.md (+ 5 traductions)
```

## Chaîne d'appel

```
bin/{script}                       # dépôt cloné : coquille de 3 lignes
  ou la commande `{script}` installée   # uvx / uv tool install : [project.scripts]
    → main() de lib/cli/{script}.py
      → run_cli(<Nom>Cli())          # lib/fire_base.py, dispatch des sous-commandes fire
        → fonction métier dans lib/{nom}.py
          → lib/ui.py / lib/exec.py / ... partagés
```

`bin/` ne contient **ni logique métier ni symlink** : chaque shell est `from lib.cli.<module> import <fn> as main` + `raise SystemExit(main())`. L'implémentation vit dans `lib/cli/<nom>.py` (un module par commande) et appelle les helpers partagés de `lib/{domaine}.py`. `merge_*` / `push_*` sont 12 shells au-dessus de `lib/cli/gitwf.py` ; chacun passe `(name, action, target)` explicitement. Les mêmes fonctions sont déclarées dans `[project.scripts]`, donc `uvx --from git+https://github.com/lazygophers/scripts <nom>` lance n'importe quel outil sans cloner. Lancer `uvx git+https://github.com/lazygophers/scripts` sans nom de commande utilise le point d'entrée `scripts`, un alias de `lazyhelp`.

Tout nouvel outil public doit être enregistré dans `TOOLS` de `lib/lazyhelp.py` (catégorie + description) ; la guidance IA va dans `COMMAND_SKILLS` de `lib/skills_help.py`.

# Structure

```
scripts/
├── bin/                          # scripts d'entrée fins (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_* / push_*          # tous des symlinks → bin/_gitwf, dispatch selon le nom
│   ├── switch_branch, sync_master, sync_branch, fetch_all, delete_branch, delete_branch_remote
│   ├── loop, unsleep, websearch, webgrab, archery, grafana, ovpn, ...
│   └── inject                    # injecte bin/ dans le PATH du shell
├── lib/                          # toute la logique (à plat, sans sous-dossiers)
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
bin/{script}            (3 lignes de path hack + import)
  → run_cli(<Nom>Cli())      # lib/fire_base.py, dispatch des sous-commandes fire
    → fonction métier dans lib/{nom}.py
      → lib/ui.py / lib/exec.py / ... partagés
```

L'entrée fine transmet juste argv à `run_cli` — **aucune logique métier ici**. `merge_*` / `push_*` sont des symlinks vers `bin/_gitwf` ; le basename de argv[0] détermine l'action et la branche cible. Les capacités partagées (opérations git, exécution, UI, notifications, ...) vivent dans `lib/{domaine}.py`.

Tout nouvel outil public doit être enregistré dans `TOOLS` de `lib/lazyhelp.py` (catégorie + description) ; la guidance IA va dans `COMMAND_SKILLS` de `lib/skills_help.py`.

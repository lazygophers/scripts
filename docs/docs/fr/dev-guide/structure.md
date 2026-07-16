# Structure des répertoires

```
scripts/
├── bin/                          # Scripts d'entrée légère (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_canary, merge_develop, merge_master, merge_test   # appelle lib git_workflow.merge_to(target)
│   ├── push_canary, push_develop, push_master, push_test       # push_to dépôt unique / automatique par lots hors git
│   ├── switch_branch, sync_master, sync_branch, fetch_all, delete_branch, delete_branch_remote
│   ├── loop, unsleep, reindex
│   └── inject                    # injecter bin/ dans le PATH shell
├── lib/
│   ├── commands/{domaine}/{commande}.py    # Logique métier de chaque commande, expose main(argv) -> int
│   │   ├── build/  file/  git/  process/  misc/  system/
│   │   └── git/merge.py + git/push.py expose également run(target, argv)
│   └── {domaine}.py                    # Bibliothèque partagée (git/exec/ui/notify/build/process/...)
├── tests/                        # Suite unittest
├── commit / mr / issue          # Scripts bash, à réécrire en py (gardés temporairement à la racine)
└── README.md
```

## Chaîne d'appel

```
bin/{script}            (3 lignes de hack de chemin + import)
  → lib.commands.{domaine}.{commande}.main(argv)
    → Bibliothèque partagée lib/{domaine}.py
```

Les entrées légères ne transmettent que les argv au module métier, **n'écrivent pas de logique métier**. Les capacités partagées (opérations git, exécution de commandes, UI, notifications, détection de construction, gestion des processus...) sont sédimentées dans `lib/{domaine}.py`, réutilisables entre les commandes.

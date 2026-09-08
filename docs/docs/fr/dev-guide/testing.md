# Tests

```bash
python3 -m unittest discover -s tests -q
```

La suite est dans `tests/` à la racine. Les nouvelles commandes devraient avoir des tests unitaires : la logique se teste dans `lib/{nom}.py` ; les entrées fines ne font que transmettre. `tests/test_lazyhelp.py` vérifie la cohérence bin/ ↔ `TOOLS` — pensez à enregistrer les nouvelles entrées.

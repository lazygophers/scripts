# Tests

```bash
python3 -m unittest discover -s tests -q
```

La suite de tests se trouve dans `tests/` à la racine du dépôt. Il est recommandé d'ajouter des tests unitaires correspondants pour les nouvelles commandes, en maintenant la granularité de test séparée entre l'entrée légère et la logique métier (la logique métier est testée sous `lib/commands/`, les entrées légères ne transmettent que les données et n'ont pas besoin de tests séparés).

# Testing

```bash
python3 -m unittest discover -s tests -q
```

The suite lives at the repo root `tests/`. New commands should ship matching unit tests — keep the thin-entrypoint/business split when testing (test business logic under `lib/commands/`; thin entrypoints only forward and need no separate test).

# Testing

```bash
python3 -m unittest discover -s tests -q
```

The suite lives in `tests/` at the repo root. New commands should add unit tests: business logic is tested in `lib/{name}.py`; thin entrypoints only forward and need no separate test. `tests/test_lazyhelp.py` verifies bin/ entries match the `TOOLS` registry — remember to register new entrypoints.

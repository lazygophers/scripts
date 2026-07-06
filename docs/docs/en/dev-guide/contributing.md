# Contributing

- PRs must include necessary unit tests.
- New scripts follow the [Add a Script](./add-script.md) two-step — keep thin entrypoint and business logic separated.
- Put shared capabilities in `lib/{domain}.py` for cross-command reuse; avoid duplicating code in entrypoints or single commands.
- Use standard `git` for Git operations, avoid interactive wrappers; always check working-tree cleanliness and provide rollback before automation.

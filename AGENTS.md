<!-- TRELLIS:START -->
# Trellis Instructions

These instructions are for AI assistants working in this project.

This project is managed by Trellis. The working knowledge you need lives under `.trellis/`:

- `.trellis/workflow.md` — development phases, when to create tasks, skill routing
- `.trellis/spec/` — package- and layer-scoped coding guidelines (read before writing code in a given layer)
- `.trellis/workspace/` — per-developer journals and session traces
- `.trellis/tasks/` — active and archived tasks (PRDs, research, jsonl context)

If a Trellis command is available on your platform (e.g. `/trellis:finish-work`, `/trellis:continue`), prefer it over manual steps. Not every platform exposes every command.

If you're using Codex or another agent-capable tool, additional project-scoped helpers may live in:
- `.agents/skills/` — reusable Trellis skills
- `.codex/agents/` — optional custom subagents

## Subagents

- ALWAYS wait for every spawned subagent to reach a terminal status before yielding, acting on partial results, or spawning followups.
  - On Codex, this means calling the `wait` tool with the subagent's thread id (requires `multi_agent_v2`). Do NOT infer completion from elapsed time.
  - On Claude Code / OpenCode, this means awaiting the Task/agent tool result before continuing.
- NEVER cancel or re-spawn a subagent that hasn't finished. If a subagent appears stuck, raise the wait timeout (Codex default 30s, max 1h) before judging it broken.
- Spawn subagents automatically when:
  - Parallelizable work (e.g., install + verify, npm test + typecheck, multiple tasks from plan)
  - Long-running or blocking tasks where a worker can run independently
  - Isolation for risky changes or checks

Managed by Trellis. Edits outside this block are preserved; edits inside may be overwritten by a future `trellis update`.

<!-- TRELLIS:END -->

<!-- BEGIN sqz-guidance (auto-installed by sqz init; remove this block to disable) -->

## sqz

Pipe shell commands with long output (git log or diff, tests, builds,
`ls -la`) through sqz:

```bash
git status 2>&1 | /opt/homebrew/bin/sqz compress
```

Don't pipe interactive commands (REPLs, `ssh`), chains with `&&`, `||`
or `;`, output redirected to a file, or short output. If that binary is
missing, run commands without it.

When the sqz MCP tools are available, prefer `sqz_read_file`, `sqz_grep`
and `sqz_list_dir` over the built-in read, grep and `ls` for long or
repeated reads, and use `sqz_recall` to find output seen earlier.

A `§ref:HASH§` stands in for output you have already seen, and
`§ref:HASH:L40-80§` for lines 40-80 of it. Get the text back with the
`expand` tool or `/opt/homebrew/bin/sqz expand <token>`. `«A1»` stands for the
phrase listed for it under `[Abbreviations]` at the top of the output.
For output without refs or abbreviations, put `SQZ_NO_DEDUP=1` or
`SQZ_NO_ABBREV=1` right before `/opt/homebrew/bin/sqz compress`.

<!-- END sqz-guidance -->

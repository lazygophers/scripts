# Issue tracker: Local Markdown

Issues and specs for this repo live as markdown files under `.scratch/`. **Not GitHub Issues** — the repo has a GitHub remote, but issue tracking is deliberately local.

## ⚠️ `.scratch/` is not version controlled

`.scratch/` is matched by the user's global gitignore (`~/.gitignore_global`), so nothing written there is committed, pushed, or visible in a PR. It lives only on the machine that created it.

This is the chosen trade-off, not an oversight. It keeps the repo clean at the cost of these documents being machine-local and lost on a fresh clone.

Practical consequences:

- A spec or decision record written here **will not appear in the PR** that implements it. Link to it by path if a reviewer needs it, and expect they may not have the file.
- Anything that must survive a fresh clone (architecture decisions others depend on, domain vocabulary) belongs in `CONTEXT.md` or `docs/adr/` instead — those are tracked. See `domain.md`.
- Before deleting a `.scratch/` directory, check whether any conclusion in it deserves promoting to a tracked file first.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`, never a single combined tickets file
- Triage state is recorded as a `Status:` line near the top of each issue file (see `triage-labels.md` for the role strings)
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

Note: `.scratch/` is also used by this repo's own task-memory convention (`.scratch/memory.md`, or `.scratch/<spec>/memory.md`). Feature directories created for issues sit alongside it and do not replace it.

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/` (creating the directory if needed). Do **not** call `gh issue create`.

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or the issue number directly.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a file with one **child** file per ticket.

- **Map**: `.scratch/<effort>/map.md` (the Destination / Notes / Decisions-so-far / Not-yet-specified / Out-of-scope body).
- **Child ticket**: `.scratch/<effort>/issues/NN-<slug>.md`, numbered from `01`, with the question in the body. A `Type:` line records the ticket type (`research`/`prototype`/`grilling`/`task`); a `Status:` line records `open`/`claimed`/`resolved`.
- **Blocking**: a `Blocked by: NN, NN` line near the top. A ticket is unblocked when every file it lists is `resolved`.
- **Frontier**: scan `.scratch/<effort>/issues/` for files that are open, unblocked, and unclaimed; first by number wins.
- **Claim**: set `Status: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` heading, set `Status: resolved`, then append a context pointer (gist + link) to the map's Decisions-so-far in `map.md`.

## PRs as a request surface

**Off.** External pull requests are not pulled into the triage queue. Flip this to on here if that changes.

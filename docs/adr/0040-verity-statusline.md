# ADR-0040: a live status line for `.verity/` state

- **Status**: Accepted
- **Date**: 2026-08-23
- **Context**: the user asked, directly, how to get VerityAI visible inside
  the Claude Code terminal itself — "as if it were an interactive bar" —
  after confirming the `PreCompact`/`SessionStart` hooks from
  [[0039-precompact-and-session-start-hooks|ADR-0039]] work but are
  otherwise invisible between compactions. Claude Code's status line
  (`statusLine` in `.claude/settings.json`) is exactly that surface: a
  persistent line at the bottom of the terminal, refreshed on session
  events, that runs a local script and displays whatever it prints — no
  API tokens spent, confirmed against the current docs
  (`code.claude.com/docs/en/statusline.md`) before implementing rather than
  guessed.

## Decision

`verity hooks statusline` reads the same JSON payload Claude Code sends
every status line command (`workspace.current_dir`/`cwd`, per the
documented `Available data` fields), discovers the nearest `.verity/`, and
prints one dim line:

```
verity: 3 dec 2 disc 1 fact | snap 002 (3m ago)
```

Reuses `MemoryStore.summary()` — the same counts `verity health` prints —
plus the latest `SnapshotManager.list()` entry's age. When
`summary()["corrupt_lines"]` is non-zero (the channel
[[0037-parse-report-discharges-invariants-5-and-6|ADR-0037]] added), a
yellow `⚠ N corrupt` segment is appended, so a truncated `.verity/` file is
visible on every keystroke, not only when someone happens to run `verity
health`. No `.verity/` found → prints nothing, so a project that never ran
`verity init` gets a silent, not broken, status line.

**`verity hooks install --statusline`** sets it, but only when no
`statusLine` is already configured — checked by presence of the key, not
merged like the two hook arrays `install()` handles. A status line is a
single slot a user may already have pointed at their own script (git
branch, a cost tracker); silently replacing it would cost them something
this tool has no way to value. The flag is opt-in on `hooks install`
precisely because it is the one piece of this integration that can visibly
clobber something the user already had.

## Consequences

- Manually verified: pointed at the `.verity/` from ADR-0039's own manual
  test (one auto-captured discovery, one snapshot), `verity hooks
  statusline` printed `verity: 0 dec 1 disc 0 fact | snap 001 (33m ago)` —
  correct counts, correct age.
- Installed on this project's own `.claude/settings.json` (alongside the
  two hooks), same dogfooding principle as ADR-0039 — not wired into the
  `Makefile` `dogfood` target or CI, since neither has an interactive
  terminal to render it in; there is nothing for an automated check to
  verify beyond what `tests/unit/test_hooks.py::TestRenderStatusline`
  already covers directly.
- `tests/unit/test_hooks.py` and `tests/integration/test_cli_hooks.py`
  cover: record counts, no-snapshot vs latest-snapshot-number, the
  corruption warning appearing only when corruption exists, reading `cwd`
  from either payload shape (`cwd` or `workspace.current_dir`), and
  `install_statusline`'s refuse-to-overwrite behavior on both a fresh and
  an already-configured `settings.json`.

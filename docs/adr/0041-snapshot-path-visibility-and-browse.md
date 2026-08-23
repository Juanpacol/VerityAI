# ADR-0041: snapshot paths are always shown, and `verity snapshots` gets `show`/`browse`

- **Status**: Accepted
- **Date**: 2026-08-23
- **Context**: raised directly by the user reading [[0040-verity-statusline|ADR-0040]]'s
  raw counts (`12 dec 18 disc 0 fact 10 fail`) — "these are ambiguous, they
  don't tell you anything, what failed, what files, so just mentioning them
  doesn't actually make a difference, because you can't manage traceability."
  Investigated first whether this pointed at `Evidence`
  (`core/models.py`, a file-locator + content-hash field that already
  exists on every `Record` but that `verity remember` never lets a caller
  fill in) — a real, separate gap, but not what was being asked for once
  clarified: the actual want was simpler and more concrete — "as
  snapshots/scratchpads get saved, tell me where; let me browse and
  reference what's in them from the CLI, so I can consult this and
  continue my work."

## The gap

`verity snapshot` printed `Snapshot 003 created` with no path — a person
who wanted to actually look at what got saved had to already know
`.verity/snapshots/NNN/snapshot.json`'s layout and go find it themselves.
`verity hooks precompact` (ADR-0039) had the same gap in its own stdout.
`verity snapshots` listed number/date/label only — no way to see a
snapshot's actual contents without opening the JSON file by hand.

## Decision

**Every snapshot-creating path now reports its file path.** `verity
snapshot`'s output gained a `saved to: <path>` line;
`capture_precompact`'s result dict (and `verity hooks precompact`'s stdout)
gained `snapshot_path` alongside `snapshot_number`. Both read from a new
`SnapshotManager.path_for(number) -> Path`, extracted from the
already-private `_dir_for` so `create()` and every caller agree on exactly
where a given snapshot lives, rather than each constructing the path
separately.

**`verity snapshots` becomes a small sub-app**, not a rename — bare `verity
snapshots` still lists (unchanged output, unchanged tests), now via a
`typer.Typer(invoke_without_command=True)` callback so the existing
zero-argument behavior survives alongside two new subcommands:

- `verity snapshots show <N>` — full contents of one snapshot: task,
  decisions, constraints, discoveries, failures, facts, each printed by
  its actual statement text, plus the path and `git_sha`. Distinguishes
  "no such snapshot" from "exists but is unreadable"
  ([[0037-parse-report-discharges-invariants-5-and-6|ADR-0037]]'s
  `get_report`), same as `restore` already does.
- `verity snapshots browse` — a numbered-list-then-prompt loop: list, ask
  for a number (or `q`), show that snapshot's full contents, loop back to
  the list. Explicitly not a curses TUI — "interactive" here means the
  prompt stays open across multiple looks in one sitting, plain
  `typer.prompt` over stdin, matching this project's existing CLI style
  (`rich` is a declared dependency, pulled in transitively by `typer`, but
  nothing in `src/` has ever imported it directly — this stays consistent
  with that rather than introducing a new rendering style for one command).

Both subcommands share `_render_snapshot(snap, path)`, so `show` and
`browse` can never format a snapshot's contents differently from each
other.

## Consequences

- Manually verified end to end: `verity snapshot "prueba manual"` printed
  `saved to: /.../.verity/snapshots/002/snapshot.json`; `verity snapshots
  show 2` printed the full section-by-section breakdown including the
  actual discovery statement; `verity snapshots browse` (piped `1\nq\n`)
  showed snapshot 001's contents, returned to the list, then exited
  cleanly on `q`.
- `tests/integration/test_cli.py` gained coverage for the path line on
  creation, `show`'s full-contents output and its missing-number failure,
  and `browse`'s pick-then-quit loop and its no-snapshots-yet message.
  `tests/unit/test_snapshot.py` confirms `path_for` matches exactly where
  `create()` writes. `tests/unit/test_hooks.py` confirms
  `capture_precompact`'s `snapshot_path` points at a file that actually
  exists.
- The separate gap this investigation surfaced but did not build —
  `Evidence`-based file/line traceability on `Decision`/`Discovery`
  /`Failure` records themselves, and the staleness check
  `ContextHealth.stale_count`'s own docstring already promises but that no
  code in this project computes — remains open. Worth a future ADR of its
  own rather than folding it into this one, since it changes what a record
  *is* (adds real evidence at write time), not just how existing records
  are surfaced.

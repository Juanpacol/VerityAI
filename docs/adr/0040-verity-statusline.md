# ADR-0040: a live status line for `.verity/` state

- **Status**: Superseded by [[0042-statusline-redesign-single-verdict-line|ADR-0042]]
  — the two-line format and its exact segments described below no longer
  match the shipped code. Kept for the reasoning trail (why a status line
  at all, why it reads `context_window`/`transcript_path`, the performance
  benchmark), not as documentation of the current output.
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
every status line command, discovers the nearest `.verity/`, and prints up
to two dim lines — asked for directly by the user after the first version
shipped: raw record counts alone don't answer "how degraded is this
context" or "how close am I to running out of room."

```
verity: 3 dec 2 disc 1 fact | 2 snapshots (latest: 002, 3m ago)
context: 62% used of 200,000 tokens (38% left) | degraded: 2% | critical retained: 100%
```

**Line 1** reuses `MemoryStore.summary()` — the same counts `verity health`
prints — plus the *count* of snapshots saved so far (not just the latest
one's age), since "how many scratchpads have actually been captured" was
asked for by name. A yellow `⚠ N corrupt` segment appends when
`summary()["corrupt_lines"]` is non-zero (the channel
[[0037-parse-report-discharges-invariants-5-and-6|ADR-0037]] added), so a
truncated `.verity/` file is visible on every refresh, not only when
someone happens to run `verity health`.

**Line 2** answers two different questions side by side, deliberately not
collapsed into one number: `context_window.used_percentage` /
`remaining_percentage` — Claude Code's own count, read straight from the
payload, no computation — answers "how soon until this session is forced
to compact." `degraded` and `critical retained` are `ContextHealth
.redundancy` / `.critical_retained`, computed live from `transcript_path`
through the *same* `classify_all` pipeline `verity context` uses (via a
new shared helper, `_classify_transcript`, also used by
[[0039-precompact-and-session-start-hooks|ADR-0039]]'s `capture_precompact`
— one parse path, so the two can never disagree about what a transcript
contains). A window can read 90% full while mostly redundant filler, or
40% full but already missing critical retention under a tight budget
elsewhere — the raw percentage alone cannot distinguish either case, which
is the whole reason this project's `ContextHealth` model reports multiple
dimensions rather than one score (`core/models.py`'s own stated rationale,
predating this ADR).

Line 2 degrades independently of line 1: no `context_window` in the
payload (can be `null` early in a session, per Claude Code's own docs) and
no readable `transcript_path` together mean line 2 is omitted entirely;
either one alone still prints. No `.verity/` at all → prints nothing, so a
project that never ran `verity init` gets a silent, not broken, status
line.

**Performance, checked rather than assumed:** re-parsing and re-classifying
the full transcript on every statusline refresh is the honest cost of a
live number instead of a cached one. Measured against this project's own
largest real session transcript (13MB, ~3,900 items):
read+parse+measure+classify+health totaled ~0.38s, dominated by token
measurement (`ContextPipeline.measure`, ~0.25s of it). Acceptable given
Claude Code's own debounce (rapid triggers batch to one run) and
cancel-in-flight behavior (a stale run gets superseded, not stacked) —
worth revisiting with caching keyed on the transcript's mtime if a much
larger session ever makes this noticeably laggy in practice.

**`verity hooks install --statusline`** sets it, but only when no
`statusLine` is already configured — checked by presence of the key, not
merged like the two hook arrays `install()` handles. A status line is a
single slot a user may already have pointed at their own script (git
branch, a cost tracker); silently replacing it would cost them something
this tool has no way to value. The flag is opt-in on `hooks install`
precisely because it is the one piece of this integration that can visibly
clobber something the user already had.

## Consequences

- Manually verified against this project's own real 13MB session
  transcript with a realistic `context_window` payload:
  ```
  verity: 0 dec 1 disc 0 fact | 1 snapshot (latest: 001, 41m ago)
  context: 62% used of 200,000 tokens (38% left) | degraded: 2% | critical retained: 100%
  ```
  Correct counts, correct percentages, sub-second.
- Installed on this project's own `.claude/settings.json` (alongside the
  two hooks), same dogfooding principle as ADR-0039 — not wired into the
  `Makefile` `dogfood` target or CI, since neither has an interactive
  terminal to render it in; there is nothing for an automated check to
  verify beyond what `tests/unit/test_hooks.py::TestRenderStatusline`
  already covers directly.
- `tests/unit/test_hooks.py` and `tests/integration/test_cli_hooks.py`
  cover: record counts, snapshot count vs no-snapshots, the corruption
  warning, reading `cwd` from either payload shape, context-window
  rendering (including the ≥80%-used color threshold), degradation
  rendering from a real fabricated transcript, line 2 degrading gracefully
  when only one of `context_window`/`transcript_path` is usable, and
  `install_statusline`'s refuse-to-overwrite behavior on both a fresh and
  an already-configured `settings.json`.
- **Named limitation:** no caching. A future session with a transcript an
  order of magnitude larger than the one benchmarked here could make the
  live re-classification noticeably slow; the fix (cache keyed on the
  transcript file's mtime) is deliberately not built ahead of evidence
  that it's needed.

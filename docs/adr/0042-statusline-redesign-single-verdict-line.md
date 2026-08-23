# ADR-0042: statusline redesign — one verdict line, `verity status` for detail

- **Status**: Accepted
- **Date**: 2026-08-23
- **Context**: direct feedback on [[0040-verity-statusline|ADR-0040]]'s
  first version (`verity: 12 dec 18 disc 0 fact 10 fail | 3 snapshots
  ...`): raw counts are ambiguous to a working engineer — no traceability
  to *what* failed or *which* file, so a bare number "doesn't actually make
  a difference." The user's own redesign proposal named the right
  question directly: "the bar should answer 'is Verity healthy and is the
  agent still working with good context', not show every internal
  metric" — detect at a glance, diagnose on demand.

## What was proposed, and what of it is real

The proposal's shape is adopted near-verbatim: a status dot
(healthy/degraded/critical), a compact one-line summary, and an expandable
detail view reached separately. Two pieces of the proposal were not built,
on investigation:

- **`Contradictions: N`** in the detail view — `ContextHealth
  .contradiction_count` (`core/models.py`) exists as a field, but nothing
  in this codebase computes a real value for it; every call site leaves it
  at its default `0`. Displaying that `0` next to genuine signals would
  read as "checked, none found" for a dimension that has never been
  checked — the exact shape [[0029-unrankable-memory-is-not-irrelevant-memory|ADR-0029]]
  and this project's T6 finding both warn against. `verdict()` and `verity
  status` both omit it rather than show a number that lies by omission.
- **`drift: low`** — no drift-detection mechanism (memory/docs/architecture
  vs. real code) exists anywhere in this project. Shipping a hardcoded
  `low` would be worse than the two problems above combined: not an
  unwired zero, an invented one. Not built. If wanted, it is a real new
  engine (comparing recorded state against the graph/`reliability/` layer
  for actual drift), not a statusline change, and belongs in its own ADR.

Also corrected in the proposal: Claude Code's status line is not
interactive — it is a passive line re-rendered on session events, not a
menu a developer clicks into. "Expandable" here means a separate command
(`verity status`), not a collapsible UI element inside the bar itself.

## Decision

**One line**, replacing ADR-0040's two:

```
verity ● healthy | ctx 63% | crit 100% | 12D 10F | 0⚠
```

A new shared function, `cli/hooks.py::verdict(health, summary,
snapshot_age_days) -> (status, reasons)`, is the single source of truth
for the dot's color/word — used by both `render_statusline` and the new
`verity status` command, so the short line and the detailed view can never
disagree about whether something is wrong. Thresholds are editorial (same
disclaimer `ContextHealth.score`'s own docstring already carries for its
weights), not measured: `critical` means something has demonstrably gone
wrong (`.verity/` corruption, or a confirmed loss of CRITICAL context —
`critical_retained < 1.0`); `degraded` means attention is warranted soon
(window ≥85% full, ≥25% of context redundant/obsolete, or the latest
snapshot is ≥7 days old); `healthy` otherwise.

The line shows: the verdict dot; `ctx N%` (Claude Code's own
`context_window.used_percentage`, no `% left` — redundant against `%
used`, per direct feedback); `crit N%` (`critical_retained`, only the
single most load-bearing dimension, not the whole `ContextHealth`
breakdown); `ND MF` (decisions and failures only — the two record types
this project's own docs call out as the ones an agent is worst at
retaining and most expensive to lose, not all five types); and one alert
count (`.verity/` corruption + stale-snapshot flag, more sources folded in
as they become real).

**`verity status [transcript]`** is the new expanded, sectioned view:

```
VERITY STATUS

Context
  Usage:               63.0%
  Relevance:           91.0%
  Redundancy:           8.0%
  Tool noise:           4.0%

Memory
  Decisions:              12
  Discoveries:             18
  Failures:                10

Integrity
  Critical retained:  100.0%

Snapshots
  Total:                    3
  Latest:                 14d ago

Status
  ● HEALTHY
```

Deliberately a new command, not a rewrite of `verity health` — `verity
health`'s exact output (`CONTEXT HEALTH`/`PERSISTED STATE`) is asserted on
by existing tests and is this project's older, still-valid contract for a
flat dump; `verity status` is additive, sectioned, and ends in the same
`verdict()` call the status line uses, which `verity health` does not.

## Consequences

- Manually verified: the redesigned line against this project's own 13MB
  session transcript correctly read `degraded` once the fixture's snapshots
  crossed the 7-day staleness threshold — the threshold firing on real
  data, not just a unit test.
- `tests/unit/test_hooks.py::TestVerdict` covers every threshold
  independently (corruption → critical regardless of health; lost critical
  context → critical; high window usage, high redundancy, stale snapshot →
  degraded, each in isolation; nothing wrong → healthy) and explicitly
  asserts `contradiction_count` never produces a reason string.
  `TestRenderStatusline` and `tests/integration/test_cli.py::TestStatus`
  cover the one-line format and the sectioned view respectively, including
  that neither ever mentions "contradiction."
- ADR-0040's two-line format and its `_memory_line`/`_context_line`
  functions are superseded by this ADR, not merely amended — this document
  is authoritative for the current statusline shape.
- **Named, still open**: the same two gaps the proposal surfaced remain
  real future work — wiring the existing Consistency Engine
  (`consistency/check.py`) to actually populate `contradiction_count`, and
  a genuine drift-detection engine. Neither is a statusline problem; both
  are engine work with their own ADR when built.

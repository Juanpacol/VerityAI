# ADR-0043: `verdict()` pairs reasons with actions, and drops a signal that could never fire

- **Status**: Accepted
- **Date**: 2026-08-23
- **Context**: direct follow-up question on
  [[0042-statusline-redesign-single-verdict-line|ADR-0042]] — "when
  `degraded` shows, what is the user supposed to *do* with our tool?"
  Answering it honestly required first checking exactly what
  `crit 100%`/`Critical retained: 100.0%` was actually measuring, since a
  prescribed action is only meaningful if the signal behind it is real.

## The bug this surfaced

`context/health.py::compute_health()` hardcodes `critical_retained=1.0`,
unconditionally, with its own docstring explaining why: "nothing has been
pruned yet at measurement time, so every critical item is by definition
still present. This dimension only becomes informative when comparing a
pruned context against its original" — which is exactly what the sibling
function `critical_retention(before, after)` is for. `verdict()`
(ADR-0042) called `compute_health()` on a raw, unpruned transcript in
every code path that reaches it (the status line, `verity status`) — so
its `health.critical_retained < 1.0` critical-tier trigger could
*mathematically never fire*. Not unlikely — impossible, by construction.

This is the same shape as the `contradiction_count` gap
[[0041-snapshot-path-visibility-and-browse|ADR-0041]] found and
[[0042-statusline-redesign-single-verdict-line|ADR-0042]] deliberately
excluded — a value that reads as "checked, none found" while never having
been checked at all — except worse in one respect: an unwired `0` at
least has the shape of "nothing measured yet"; a value permanently pinned
at `1.0` (100%) looks like active reassurance. It shipped in ADR-0042
itself, found only because the user's next question forced tracing what
the number actually meant rather than accepting it at face value.

## Decision

**Removed** `critical_retained` as a `verdict()` trigger and as a
displayed statusline segment entirely — there is no honest way to show it
from a single unpruned transcript. Replaced with a value that is real and
does vary: a live count of items the classifier currently calls CRITICAL
in the transcript (`N crit`, using the same `classify_all` pipeline
`_classify_transcript` already runs). It measures something different and
weaker — "how much of what's here is load-bearing," not "was anything
lost" — but it is not fabricated.

**`verdict()`'s return type changes** from `list[str]` reasons to
`list[tuple[str, str]]` — `(reason, action)` pairs, answering the question
that prompted this ADR directly: every reason `verdict()` can produce now
carries the specific `verity` command that addresses it.

| Trigger | Action |
|---|---|
| `.verity/` corruption | `verity health` to find the file/line, fix or delete by hand |
| Window ≥85% full | `verity context <transcript> --budget N --task '...'` now, or trust the PreCompact hook |
| Redundancy ≥25% | Same prune command |
| Snapshot ≥7 days old | `verity snapshot` |

`render_statusline` appends `-> verity status` when the verdict is not
`healthy`, so the short line points at where the paired actions live
rather than leaving a bare word to interpret. `verity status`'s `Status`
section prints each reason with its action on the following line.

## Consequences

- Manually verified: a hand-corrupted `.verity/` produces `● CRITICAL` /
  `1 corrupt line(s) in .verity/` / `-> run \`verity health\` to see which
  file/line, then fix or delete it by hand` — both in the one-line
  statusline (pointer only) and in `verity status`'s full pairing.
- `tests/unit/test_hooks.py::TestVerdict::test_critical_retained_is_never_a_trigger`
  pins the fix directly: a `critical_retained=0.8` health object (a value
  that could never actually occur through this module's own call path) no
  longer produces a `critical` verdict — the previous test asserting the
  opposite was itself encoding the bug and is replaced, not merely
  updated.
- Every other `TestVerdict` case now asserts on the `(reason, action)`
  tuple shape, and confirms the specific action string for the corruption
  and stale-snapshot triggers.
- **Named, not fixed here**: a real "was anything critical actually lost"
  signal would need to diff the live transcript against what a real
  compaction preserved (or against `verity context`'s own before/after
  when a human runs it deliberately) — this ADR removes a fake version of
  that signal, it does not build the real one. Worth its own ADR if the
  before/after artifact becomes available (e.g. Claude Code exposing what
  a compaction actually dropped).

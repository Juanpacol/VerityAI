# ADR-0045: `render_health()` had the same fake-signal trap ADR-0043 fixed elsewhere

- **Status**: Accepted
- **Date**: 2026-08-23
- **Context**: the user ran the newly-added `/verity-health` slash command
  ([[0041-snapshot-path-visibility-and-browse|ADR-0041]]) against this
  session's own (very large, genuinely 100%-full) transcript and got back
  `Critical retained 100.0%` and `Contradictions 0`, printed with no
  caveat. [[0043-verdict-actions-and-the-fake-critical-retained-signal|ADR-0043]]
  found and fixed the exact same two fake signals *in one place* —
  `cli/hooks.py::verdict()`, built the same day — but never checked
  whether the underlying cause was local to that one function. It was not.

## The defect

`compute_health()` (`context/health.py`) hardcodes `critical_retained=1.0`
and defaults `contradiction_count=0`, both for the reason its own docstring
already states: this dimension is only meaningful when comparing a pruned
context against its original, which is what the separate function
`critical_retention(before, after)` is for — `compute_health()` alone never
receives that comparison. Grepping every call site of `compute_health()`
confirmed the real shape of the problem: `verity ingest`, `verity health`,
`verity context --adaptive`, and the MCP `context(op="health")` op all call
it directly on a single, unpruned item list — never with a before/after
pair. `critical_retention()` itself is called from exactly two places,
neither of which is `render_health()`'s caller: `bench/deterministic.py`
(Family A) and `cli/main.py:243` inside the real `verity context` prune
command. So every one of `render_health()`'s callers displays
`Critical retained: 100.0%` as if it were a live measurement, and it is
structurally incapable of ever reading anything else through that path —
the same "checker that has never failed" shape this project's own T6
finding warns about, just spread across four call sites instead of one.

## Decision

`compute_health()` now unconditionally appends two notes to
`ContextHealth.notes` — the channel this project already uses for exactly
this purpose (invariant 5) — stating plainly that `critical_retained` is
always 100% through this function and that `contradiction_count` is always
0 because nothing computes it. `render_health()` already prints `notes`
last, after every component and the aggregate score, so both caveats reach
every caller (`verity ingest`, `verity health`, `--adaptive`, the MCP
health op) automatically, with no per-caller change required.

The fields themselves are left as-is — `1.0` and `0` remain technically
correct defaults, and `verity context`'s real prune path still reports the
genuine value via `critical_retention()`, untouched by this change. This
ADR is purely about not letting the constant read as a measurement outside
that one real path.

## Consequences

- Manually verified: `echo '[...]' | verity health -` now prints both
  notes at the end of the report, every time, on a repo this large or a
  one-line fixture alike.
- `tests/unit/test_health.py` gained
  `test_critical_retained_is_flagged_as_structurally_constant` and
  `test_contradiction_count_is_flagged_as_unmeasured`, both asserting the
  note text directly rather than just the field's value.
- This is the second time in one day the same trap was found in two
  different places built hours apart
  ([[0043-verdict-actions-and-the-fake-critical-retained-signal|ADR-0043]],
  now this) — worth naming as a pattern: any new surface built on top of
  `ContextHealth` should check `compute_health()`'s own docstring caveat
  before assuming `critical_retained` or `contradiction_count` mean
  anything, rather than rediscovering this by reading real output.

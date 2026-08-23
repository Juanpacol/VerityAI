# ADR-0044: `verity failures resolve` — closing the loop `remember failure` never had

- **Status**: Accepted
- **Date**: 2026-08-23
- **Context**: the user asked to see the 10 failures behind the status
  line's `10F`. Reading `.verity/state/failures.jsonl` by hand — this
  project's own real store — showed all 10 marked `resolved: false`,
  though several carry their own fix described in the `error` text (e.g.
  #7's "found by `verity reliability architecture`", #10's "Corregido con
  un lookaround..."). The reason: `remember failure`
  (`cli/main.py`) only ever appends a new `Failure`; nothing anywhere in
  this codebase ever set `resolved=True` on an existing one. The unresolved
  count `summary()["failures"]` feeds — the same count the status line
  ([[0042-statusline-redesign-single-verdict-line|ADR-0042]]) and `verity
  status` ([[0043-verdict-actions-and-the-fake-critical-retained-signal|ADR-0043]])
  both display — could only grow, never shrink, regardless of how much
  work actually got done.

## Decision

`MemoryStore.resolve_failure(failure_id, note="")` mirrors
`Decision.supersede()` exactly ([[0036-supersede-never-deactivated-the-original|ADR-0036]]):
appends a marker `Failure` record (`resolves=failure_id`) rather than
rewriting the original line — the same append-only reasoning applies
identically here. `Failure` gains a `resolves: UUID | None` field,
parallel to `Decision.supersedes`.

`MemoryStore.failures()` follows the chain on read: any original whose id
appears in another record's `resolves` gets reclassified to
`resolved=True`, and marker records themselves are excluded from the
returned list — a resolution event is metadata about the log, not a new
failure. Getting this filtering right the first time mattered:
[[0036-supersede-never-deactivated-the-original|ADR-0036]]'s bug was
precisely a chain-following step that was promised in a docstring and
never implemented; this ADR does not repeat that.

**CLI**: `verity failures` lists every failure, numbered oldest-first with
a `[✓]`/`[ ]` marker and the open/total count; `verity failures resolve
<N> [--note "..."]` closes the Nth one by that stable numbering (append-only
means earlier numbers never shift), refusing cleanly on an out-of-range
number or a no-op on an already-resolved one.

## Consequences

- Manually verified end to end: two failures recorded, `verity failures`
  lists both open; `verity failures resolve 1 --note "fixed by Y"` marks
  the first closed; the statusline's failure count and `verity health`'s
  count both drop from 2 to 1 immediately after, with no other command
  run in between.
- `tests/unit/test_memory.py::TestFailureResolution` covers: the
  unresolved count actually drops, the original line is never rewritten
  (`store.read(Failure)` still shows `resolved=False` on it directly),
  the marker never appears as a second visible failure, and resolving one
  failure leaves an unrelated open one untouched.
  `tests/integration/test_cli.py::TestFailures` covers the numbered
  listing, resolve-then-relist, an out-of-range number, and resolving
  twice being a no-op rather than a duplicate marker.
- **Not done here**: this project's own 10 real, still-open failures in
  `.verity/state/failures.jsonl` are not resolved by this ADR — that is a
  separate, deliberate act of going through each one and deciding whether
  it is genuinely fixed, not something this commit should do silently on
  the record's behalf.

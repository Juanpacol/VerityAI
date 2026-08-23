# ADR-0037: `ParseReport` discharges invariants 5 and 6 in `memory/`

- **Status**: Accepted
- **Date**: 2026-08-23
- **Context**: found auditing `memory/store.py` for the same class of defect
  [[0034-dropped-critical-was-dead-code|ADR-0034]],
  [[0035-enforce-budget-tiebreak-was-inverted|ADR-0035]], and
  [[0036-supersede-never-deactivated-the-original|ADR-0036]] found in
  `context/` — a component reporting success while quietly doing less than
  its own contract promises.

## The defect

`MemoryStore.read()` swallowed every malformed JSONL line with `except
Exception: continue`, returning a silently shortened list. No caller could
tell "no records" from "the file is corrupted." Every typed accessor
inherited this — `decisions()`, `constraints()`, `discoveries()`,
`failures()`, `facts()`, `surfacings()` — so `summary()` and `verity health`
reported a plausible smaller number instead of a corruption warning,
violating this project's own invariant 6 ("parsing never loses input; the
parts must sum to the whole") and invariant 5 ("every degraded path says
why").

The same defect, same shape, existed at four other sites: `task()` returned
`None` for both "no task set" and "task.json is corrupt"; `SnapshotManager
.get()`/`list()` had the identical ambiguity; `context/ingest.py`'s `load()`
computed a skip count from `parse_jsonl` and discarded it (the docstring
admitted this); `context/rank.py`'s `_semantic_rank` reported
`degraded_reason` only when *every* document's embedding failed, so a
partial failure was invisible.

One of the five was more than a reporting gap. `SnapshotManager.create()`
reads through six accessors and writes the result as a `Snapshot` that
*looks* complete; `restore()` later writes that shortened history forward as
new records. A read defect there becomes a **permanent write defect** — the
corruption gets laundered into a clean-looking artifact and then
re-propagated.

## Decision

**A new model, `ParseReport`** (`core/models.py`): `source`, `exists`,
`lines_seen`, `parsed`, `skipped: dict[int, str]` — keyed by line number,
not by reason, because in a hand-edited append-only JSONL file the line
number is the address a user goes and fixes (`sed -n '42p' ...`), the same
role a path plays in `graph/ingest.py`'s `IngestReport.skipped`. A derived
`sums_to_whole` property makes invariant 6 arithmetic rather than a promise.

**`read()` keeps its signature.** A stateless sibling, `read_report(model)
-> (records, ParseReport)`, carries the new channel; `read()` becomes
`read_report(model)[0]`. A `store.last_read_report` attribute was considered
and rejected: `summary()` calls `read()` (now `read_report()`) eight times,
so a single mutable slot would destroy seven of eight reports before
anyone could read them, and would make `MemoryStore` — today a pure path
wrapper with no mutable state — stateful for no reason, the exact seam
[[0028-the-mocked-test-that-could-not-fail|ADR-0028]] says a test should not
need a mock to reach.

**The six typed accessors are unchanged.** The skip channel belongs to the
file, not to the filter over it. A new `integrity() -> list[ParseReport]`
aggregates one report per backing file plus `task.json`, and is the single
entry point `verity health`, the handoff footer, and the MCP surface use.
`summary()` gains `corrupt_lines`/`corrupt_files`, always present and `0`
when clean.

**`SnapshotManager.create()` refuses on corrupt state** rather than
tolerating it, via a new `CorruptStateError`, with `--force`/`force=True`
to override. Every other read in this change tolerates a bad line and
reports it; `create()` is the one write path where tolerating it would
convert a read defect into a permanent one.

The four siblings got the same shape where it fit and deliberately not where
it didn't: `task()` gained `task_report()`; `SnapshotManager.get()`/`list()`
gained `get_report()` and their own `integrity()`. `context/ingest.py`'s
`load()` gained `load_report()` — but **kept** its count-by-reason `dict[str,
int]` shape rather than converting to `ParseReport`: a 20,000-line session
file where hundreds of lines share one reason needs a summary, not a report
naming every line, and `ingest_claude_code.parse_jsonl` already owns that
instrument correctly. `context/rank.py`'s partial-embedding case needed no
new model at all — just a `failed` counter feeding the `degraded_reason`
field that already existed, with `mode` staying `"hybrid"` because it
genuinely was for most documents.

## Consequences

- `verity health` against a `.verity/` with a truncated final line now
  prints a `CORRUPTION` block naming the file, the line, and the reason,
  instead of silently reporting one fewer decision. Verified manually
  against a hand-corrupted store: `state/decisions.jsonl: 2/3 lines parsed;
  line 3 invalid JSON: ...`.
- `verity restore <n>` and the MCP `restore` op now distinguish "no such
  snapshot" from "that snapshot exists but is unreadable" — previously both
  read as `No snapshot NNN`, which sent a user looking for a snapshot that
  was right there on disk.
- `verity snapshot` and the MCP `snapshot` op refuse over corrupt state by
  default, with the exact corrupt line named in the refusal and `--force`
  offered as the explicit override.
- The handoff document's token footer now carries an `[incomplete]` warning
  when any source it drew from was corrupt — an agent receiving a handoff
  must not be told a truncated history is the whole one.
- One new test file section per touched module
  (`test_memory.py::TestParseReport`, `test_snapshot.py
  ::TestCorruptStateRefusal`, `test_handoff.py::TestCorruptionReporting`,
  `test_rank.py`'s partial-failure cases, `test_ingest.py::TestLoadReport`,
  plus CLI integration tests), all `tmp_path` and hand-written corrupt
  JSONL, no mocks.
- **Named trade-off:** `summary()` now calls `integrity()` (which itself
  calls `read_report()` once per file) and then the six typed accessors
  separately (which call `read()`, i.e. `read_report()` again) — a real
  double-read this change introduces and does not eliminate. Acceptable at
  today's scale (hundreds of records per `store.py`'s own docstring, not
  millions); the moment that stops being true, `summary()` deriving its
  counts directly from `integrity()`'s already-parsed records is the
  follow-up, not a new invariant.

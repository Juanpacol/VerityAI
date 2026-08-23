# ADR-0038: atomic writes under `.verity/`

- **Status**: Accepted
- **Date**: 2026-08-23
- **Context**: found in the same production-readiness pass as
  [[0037-parse-report-discharges-invariants-5-and-6|ADR-0037]], but a
  distinct kind of gap — not a missing report, a missing guarantee.

## The defect

Several writers under `.verity/` used `Path.write_text`, which is
truncate-then-write: the file is emptied, then the new content is written.
A process killed between those two steps leaves a truncated file — not
"the old content" or "the new content," but neither. Two concurrent writers
can interleave the same way. [[0037-parse-report-discharges-invariants-5-and-6|ADR-0037]]
made a truncated file *diagnosable*; it did nothing to make one less likely.

Affected: `MemoryStore.set_task()` (`memory/store.py`), `MemoryStore.init()`'s
`config.toml` creation (a `TOCTOU` race on `if not config.exists()`, not
truncation, but the same family — two concurrent `init()` calls can
interleave), `SnapshotManager.create()`'s `snapshot.json` write, four sites
in `cli/main.py` (`--out`/`--json` file writes), and three in
`bench/evidence.py` (`changes.diff`, `scorer.txt`, `spec.json`,
`report.json`). `MemoryStore.append()` (JSONL, `O_APPEND`) and
`graph/store.py` (SQLite, transactional) were already fine and are
untouched.

## Decision

One shared helper, `core/atomic.py::atomic_write_text`: write to a temp
file in the *same directory* as the target (so the later rename stays on
one filesystem — a cross-filesystem rename is not atomic), then
`os.replace()` it over the target. `os.replace` is a single filesystem
operation on POSIX; a reader never observes a partial write, only the old
content or the new, never a mix. On any exception before the replace, the
temp file is removed and the original is untouched.

`init()`'s config-creation race gets a narrower fix: `os.open` with
`O_CREAT | O_EXCL`, making "create only if absent" one atomic syscall
instead of a check-then-write race between two concurrent `init()` calls.

Placed in `core/` rather than owned by one engine: `cli/`, `bench/`, and
`memory/` all need it, and everything already depends on `core/`
(CLAUDE.md's dependency rule), so this is zero new cross-engine edges
rather than one engine reaching into another.

**Concurrency, named rather than solved.** There is still no file locking
anywhere in this codebase. For the `O_APPEND` writes in
`MemoryStore.append()`, that is an accepted, documented gap — small-line
appends are atomic in practice on POSIX, and a lock the test suite cannot
meaningfully exercise is worse than an honest gap. This ADR fixes the
*non-atomic* writes, which were the real, demonstrable exposure; it does not
claim `.verity/` is now safe for multiple concurrent writers. The contract
remains "one writer at a time."

## Consequences

- `tests/unit/test_atomic.py` proves the mechanism directly: a write that
  raises before the rename leaves the previous file byte-identical and no
  temp file behind — the same guarantee a real kill mid-write needs, made
  deterministic via fault injection rather than an actual `kill -9` (not
  portable in a test suite).
- `tests/unit/test_memory.py::TestCorruption::test_set_task_leaves_the_previous_task_intact_if_the_write_fails`
  exercises the same proof through the real call path, not just the helper
  in isolation.
- Manually verified: `verity task` followed by a simulated crash mid-write
  leaves the prior task fully readable rather than truncated.
- No behavior change for any caller — `atomic_write_text(path, text)` is a
  drop-in replacement for `path.write_text(text, encoding="utf-8")`, same
  signature shape, same default encoding.

# ADR-0009: The first real Family A measurement, and the bug it found

- **Status**: Accepted
- **Date**: 2026-08-09
- **Context**: closing the gap `docs/BENCHMARK_PROTOCOL.md` left open since
  Phase 1 — every prior number in this project's history was synthetic.

## Context

`docs/BENCHMARK_PROTOCOL.md` states plainly that no benchmark had been run,
and records the one number that had been computed (92.4% reduction) as the
worked example of what not to publish — the fixture behind it was ~90%
duplicates by construction, so the figure measured the fixture, not the
tool. Closing that gap needed real data, and the honest source of real data
already existed on disk: Claude Code writes every session's transcript to
`~/.claude/projects/<project>/<session-id>.jsonl`, including the sessions
that built this project's own Phases 1–4.

## Decision 1: parse the real format directly, don't force it through the
generic loader

Claude Code's transcript format is an event log — one JSON object per line,
`type` in `{"user", "assistant", "mode", "attachment", ...}` — not the
`{"role", "content"}` message-array shape `context/ingest.py::from_messages`
already handles, and not simply JSON-with-newlines. `context/
ingest_claude_code.py` parses it directly: only `user`/`assistant` lines
carry content, `tool_use`/`tool_result` blocks are flattened with one more
level of recursion than the generic flattener needs, and every other line
type is counted by name in a `skipped` dict rather than silently dropped —
the same "declare what you didn't read" rule `graph/ingest.py` applies to
non-Python files. The parser was verified against all 5 real session files
in this project's own transcript directory (ranging 2KB–23MB) before a
single line of test fixture was written, and needed no hardcoded list of
"the" bookkeeping types — two event types not seen during development
(`frame-link`, `queue-operation`) were still handled correctly, because
anything outside `{"user", "assistant"}` is generic bookkeeping by
construction, not by an enumerated list.

`context/ingest.py::load()` disambiguates a single JSON array from this
JSONL format by trying to parse the whole input as one JSON value first;
when that fails, a cheap sniff (`is_claude_code_jsonl`) checks whether the
first few lines each parse as a JSON object with a `type` field before
routing to the new parser instead of the plain-text fallback.

## Decision 2: the found bug — critical-retention ground truth must dedup
before classifying, matching the pipeline's own order

Running the finished parser through `verity bench` against the 5 real
sessions immediately reported `critical retention: 98.9%–99.5%` on the three
substantial ones — a violation, apparently, of the one invariant this whole
engine exists to guarantee (`PruneResult` never drops a protected item).

It wasn't a real violation. Tracing it down: `bench/deterministic.py::
measure_case` established its "what the pipeline is obligated to keep"
baseline by classifying the *raw, pre-dedup* item list. `classify.py`'s own
precedence rules give an explicit marker priority over the duplicate check —
"a critical item stays critical even if duplicated," by design — so when a
critical-marked line appeared twice verbatim in a real session (found: index
21 and index 433 of one transcript, byte-identical), classifying the raw
list marked *both* copies critical independently. The real pipeline dedups
*before* classifying, correctly keeps only the first occurrence (the
information survives once, which is the entire point of deduplication), and
`measure_case` then compared its two-copies ground truth against the
pipeline's one-copy-survives-legitimately output and reported the second
copy as "dropped."

The fix: `measure_case` now runs the pipeline's own (newly public)
`ContextPipeline.dedup()` on the measured items before establishing the
retention baseline, matching the order the real pipeline already uses.
`dedup()` becoming public mirrors why `measure()` already was — a caller
assembling a baseline outside the full pipeline needs the same intermediate
step the pipeline itself takes. A regression test
(`TestDuplicatedCriticalMarkers` in `tests/unit/test_bench.py`) reproduces
the exact shape (a critical marker duplicated verbatim elsewhere in a
transcript) so this measurement bug cannot silently return.

This is exactly the category of finding this whole validation effort was
undertaken to surface — a false alarm in the *measurement*, not the
pipeline, findable only by running real data through it and refusing to
accept a suspicious result without tracing it to a specific line.

## Decision 3: publish two numbers, not one, and never mix Family A and
Family B in the same claim

Measured on the three substantial real sessions (the two trivial ones —
2–3 items, a `/clear` and a `/model` command — were excluded as too small to
support a claim, per the protocol's own `_MIN_ITEMS_FOR_A_CLAIM` guard):

| Configuration | Before | After | Reduction | Critical retained |
|---|---:|---:|---:|---:|
| No budget | 3,255,931 | 3,215,766 | 1.1% | 100% |
| 30,000-token budget | 3,255,931 | 1,456,908 | 55.2% | 100% |

Both numbers are real, both meet `CorpusReport.is_publishable`, and they
answer different questions on purpose. The unforced number is what dedup,
tool-output-noise filtering, and compression remove with nothing forced out
— it says real sessions carry far less exact-duplicate padding than the
synthetic fixture did. The budgeted number is what happens once a real
constraint forces a choice, which is the number that actually matters for
"can this fit in a smaller context" — and the one invariant that has to hold
under that pressure held at exactly 100% in both real runs, which is the
more important fact than either percentage.

Neither number says anything about whether Verity changes a task's outcome.
That is Family B, requires a noise floor per the protocol, and remains
unmeasured.

## Consequences

- The synthetic-fixture era of this project's benchmarking is over: every
  number in the README from this point is traceable to a real corpus and a
  reproducible command.
- Real session transcripts are never committed to the repository — only
  their aggregate token counts, via `verity bench`'s existing output shape,
  which `tests/unit/test_bench_privacy.py` pins down explicitly (a planted
  secret string in a transcript must not appear in `render_report`/`to_json`
  output).
- `ContextPipeline.dedup()` is now part of the class's public surface,
  alongside `measure()` — both exist because an external caller building a
  comparison baseline needs the same intermediate steps the pipeline uses
  internally, and duplicating that logic outside the class would be exactly
  the kind of "two implementations, two chances to disagree" problem
  `reliability/architecture.py`'s design note already warns against.

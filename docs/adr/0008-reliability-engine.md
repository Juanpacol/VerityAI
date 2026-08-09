# ADR-0008: The Reliability Engine — rescuing T6, and checking the architecture on itself

- **Status**: Accepted
- **Date**: 2026-08-09
- **Context**: Phase 4 of the harness (ADR-0005), the last of the four engines
  in the original design.

## Context

Two things were waiting for this phase: `rule_engine.py`, quarantined since
the Phase 1 pivot because its imports pointed at deleted models, and the
"deterministic first" example from the original design document — *circular
dependency? a graph algorithm, not a model call* — which Phase 2 answered for
cycles but never extended to the stronger question of whether an import goes
somewhere the architecture actually permits.

## Decision 1: rescue `rule_engine.py` unmodified; give it real models to stand on

`analysis/facts.py` (T6's fact extractors — SQL injection, check-then-act
races) was already in place since Phase 1, correctly wired to nothing. The
rule engine that consumes those facts sat in `_quarantine/` because `Rule`
and `VerificationStatus` were deleted with `ontology/models.py` during the
pivot. Phase 4 adds both back to `core/models.py` — narrower than their
pre-pivot versions (no `test_cases`, no `Pattern`, no Z3-shaped fields, since
none of that machinery exists any more) — and moves the file to
`reliability/rule_engine.py` with a one-line import fix. The logic is
untouched, including `check_for_violation`, T6's corrected inversion of a
real bug: `apply_rule_to_code` can structurally only ever return `PASS` or
`UNKNOWN`, so a rule whose precondition names a dangerous pattern got
reported `PASS` on genuinely vulnerable code. That correction is the single
most valuable thing carried across the pivot, and this phase exists partly
just to stop it sitting unused.

## Decision 2: security findings are file-granular, and every rule's blind spot is documented

`analysis/facts.py`'s extractors parse a whole module and return a flat set
of fact strings — no per-call-site line numbers. Rather than fake precision
by attributing a fact to an arbitrary line, findings are reported per file.

Running the finished scanner against this repository immediately produced a
concrete lesson in what that means: `graph/query.py`'s `context_for` and
`graph/store.py` were flagged for check-then-act race. Reading them shows
ordinary local dicts being built up (`if seed_id in found: ... else:
found[seed_id] = ...`) with no concurrent access anywhere nearby — the
syntactic shape the extractor looks for, with none of the actual concurrency
that would make it a real race. `analysis/facts.py`'s own docstring already
said this plainly ("treat a hit as worth a human look, never as proof"), but
that caveat lived only in a comment nobody reading a scan's output would see.

The fix is a small `RULE_CAVEATS` table in `security.py`: any rule that
produces a finding gets its documented blind spot printed alongside the
result, in `verity reliability security`, `check_security` (MCP), and every
test that exercises the render path. This is the same discipline as Phase
2's `untested_caveat()` — a number or a finding is never allowed to travel
without the thing that qualifies it.

## Decision 3: the architecture policy is executable, checked against the real
graph, and found real drift on its first run

`ArchitecturePolicy` encodes exactly what CLAUDE.md's "Dependency rule"
section already stated in prose — which top-level package may import which —
as a `dict[str, list[str]]` checked against the Phase 2 graph's `IMPORTS`
edges. No new parsing: one graph, checked by a second policy layered on top
of the code-structure queries Phase 2 already built, which is the reuse this
whole layered design is meant to produce.

Running it against this repository for the first time found real,
undocumented drift: `memory/handoff.py` imports `context.tokenizer`, and the
dependency diagram said `memory` depended on `core` alone. The need is
legitimate — a handoff document has to fit inside a token budget, which
means counting tokens — so the fix was to correct the documented policy to
list `memory -> context` explicitly, not to break a working import to match
a diagram that had quietly gone stale. `consistency/check.py` importing
`memory.store` (needed for decision-resurfacing checks) had the same
property and got the same treatment.

This is the outcome the check exists to produce, in both directions: it
should equally have caught the *wrong* kind of drift — an accidental import
in a direction nobody intended — and the fact that this run found only the
legitimate kind is itself informative, not a reason to trust future runs
less carefully.

## Consequences

- Zero new runtime dependencies. `reliability/` imports only `core`, `graph`,
  and `analysis` — itself now a policy-checked fact, not an assertion.
- `verity reliability security` and `verity reliability architecture` both
  exit non-zero on any finding, so either is usable as a CI gate.
- The security scanner inherits Phase 2's vendored-project exclusion
  (`graph.ingest.find_nested_projects`) — a dependency's vulnerabilities are
  not this project's to fix, and flagging them would be noise indistinguishable
  from a real finding.
- `check_architecture_at` re-ingests a throwaway graph per call for CLI/MCP
  convenience. A caller already holding an open `.verity/graph.db` (a future
  persistent-graph CLI path) should call `check_architecture` directly against
  it instead of paying that cost twice.
- The default policy is a starting point, not a ceiling: `ArchitecturePolicy`
  is a plain injectable model, and `check_architecture(store, policy=...)`
  accepts a stricter or different one — the same injection pattern
  `TokenCounter` and `ContextRanker` already use elsewhere in this codebase.

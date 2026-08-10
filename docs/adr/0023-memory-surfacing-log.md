# ADR-0023: A surfacing log — when memory was shown, not just when it was written

- **Status**: Accepted
- **Date**: 2026-08-10
- **Context**: An engineering map of `memory/` and `context/`, done to scope
  the "memory usefulness and memory timing" proposal, found something
  plain: **nothing in this codebase records a retrieval event.** Every
  timestamp in `core/models.py` is write-time — `Record.created_at`,
  `Evidence.captured_at`, `Task.updated_at`, `Snapshot.created_at`. The read
  side of `MemoryStore` (`decisions()`, `constraints()`, `facts()`, ...) is
  entirely stateless. `build_handoff` reads five categories, renders, and
  returns, leaving no trace that it ran — two identical handoffs generated
  an hour apart are indistinguishable in `.verity/`. Consequently the store
  can answer "when was this decision recorded" but not "was it ever shown
  to an agent," "how many times," or "before or after the mistake it
  describes."

## Decision

Add `Surfacing(Record)` — an observation, not a decision: which records
were shown, via what path, under what budget outcome, and (when knowable)
whether they were acted on. Two emit sites cover the two places memory
already reaches an agent today:

1. **`memory/handoff.py::build_handoff`** — the primary path. Everything a
   `Surfacing` record needs is already in scope at the point the function
   returns: the post-cut `decisions`/`constraints`/`discoveries`/`failures`
   lists (their `.id`s are the surfaced set), `dropped` (what was cut to
   fit), and the budget outcome. Recording costs nothing extra to compute.
2. **`consistency/check.py::check_decision_resurfacing`** — a resurfacing
   warning fires only when the checked text resembles a decision explicitly
   rejected or superseded. That is not just a contradiction to report; it
   is direct evidence the decision was surfaced *and* not acted on
   correctly, the one negative-usage signal this codebase can state with
   confidence. `used=False` is recorded here, not guessed at — the branch
   cannot fire unless the text still proposed the rejected thing.

For every other case, `used` stays `None` rather than `False`: whether a
handoff's contents were actually acted on is not observable from inside
the harness, and an unresolved `None` is honest where a guessed `False`
would not be.

`Surfacing` registers in `MemoryStore._FILES` as
`("memory", "surfacings.jsonl")` — `append`/`read` work unchanged, since
`_T` is bound to `Record` subclasses generically, not enumerated by value.
It is deliberately **excluded from `Snapshot`** and
`SnapshotManager.restore`: those five types (`Decision`, `Constraint`,
`Discovery`, `Failure`, `Fact`) are restorable task state; a surfacing
event is an observation about what happened, and replaying it on restore
would fabricate history that never occurred in the restored session.

## The honest ceiling, stated up front

"Did the agent *use* what was surfaced" is not fully observable from inside
the harness — nothing here sees the agent's own output or reasoning. Two
deterministic proxies exist and are what this ADR actually delivers:

- `check_decision_resurfacing` firing at all, as above — a real
  surfaced-and-ignored signal.
- Consistency-check agreement between what was surfaced and what the agent
  later claims (`consistency/claims.py` + `check.py`) — not implemented as
  part of this ADR, but the natural next correlation once both logs exist.

Anything stronger — a graded "usefulness score," a judged rating of
whether a handoff helped — would be the exact subjective composite T1
forbids. This ADR does not build one, and any future work in this area
should not either without new, non-guessed evidence.

## Consequences

- The entire "timing" research line proposed for this project (does
  surfacing early beat surfacing on demand, does an agent that saw a
  constraint three turns ago behave differently than one shown it once)
  is now measurable in principle: `Surfacing.created_at` gives a real
  retrieval timestamp to compare against `Record.created_at` (when the
  underlying decision/constraint/discovery was written) and against
  whatever timestamps a `verity eval` trial run attaches to its own
  records. Running that comparison as an actual pilot is future work, not
  part of this ADR.
- `MemoryStore.summary()` gains a `surfacings` count for parity with the
  other five record types.
- `MemoryStore.surfacings()` is a read-only accessor by design — nothing
  outside `memory/handoff.py` and `consistency/check.py` appends to this
  log, and no other caller should start doing so without updating this ADR.
- No existing behavior changes: `build_handoff`'s return value and
  `check_decision_resurfacing`'s checks are unchanged; both now have one
  additional side effect (an append to `.verity/memory/surfacings.jsonl`),
  which is inert to every existing caller and every existing test.

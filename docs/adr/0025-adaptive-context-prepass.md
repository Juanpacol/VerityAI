# ADR-0025: An adaptive context pre-pass — proactive, but never a bypass

- **Status**: Accepted
- **Date**: 2026-08-10
- **Note on numbering**: the implementation plan this work follows
  originally reserved ADR-0024 for this decision; Phase 3 (Reality Check
  expansion) used 0024 first, so this is 0025. Risk-Adaptive Verification
  (a later phase) shifts to 0026 for the same reason.
- **Context**: everything in `context/` is reactive — given a context and a
  task, `prune.py`/`rank.py`/`classify.py` decide what to drop or how to
  order it. An engineering map done to scope the "Adaptive Context Engine"
  proposal found three things genuinely missing: no function computes a
  **trigger** (is now the moment to push something), no function returns
  **candidates not already in the context**, and no **budget policy**
  beyond a flat `int | None` passed in by the caller.

## Decision

Add `context/adaptive.py` as a thin pre-pass — three pure functions,
`should_surface`, `plan_budget`, `select` — plus `memory/surface.py` for
candidate sourcing (forced into `memory/` rather than `context/` by
CLAUDE.md's dependency rule: `context/` must never import `memory/`).

**The rule that matters more than the functions themselves:** this module
only ever produces a `list[ContextItem]` for the caller to merge with the
rest of the context and hand to the existing `ContextPipeline.run`,
unchanged. It never bypasses that pipeline and never injects between its
stages. Three concrete failure modes this rule prevents, each traced to a
specific mechanism in `prune.py`:

1. **Ledger corruption (invariant 2).** `ContextPipeline._stage` is the
   sole writer of the token ledger — each stage's `tokens_before` is
   recomputed from exactly the list the previous stage returned. Injecting
   items outside that chokepoint (before `measured`, between two `_stage`
   calls, or after `_place`) corrupts `tokens_before`, `tokens_after`, or
   both, and therefore `reduction_ratio` — this project's headline number.
2. **The dedup-before-classify trap.** `dedup` runs first and keys on
   `content_hash`. A pushed memory record duplicating existing context text
   is dropped at that stage, before `classify` ever gets to protect it as
   `CRITICAL` — so a naive merge-then-classify assumption silently loses
   exactly the content a push was meant to add.
3. **The missing-task trap.** `_enforce_budget` sorts candidates by
   `rank_score`, populated only when the rank stage actually ran against a
   non-empty `task` (`ContextPipeline.run` skips ranking entirely for an
   empty task). `select()` refuses to run with an empty task rather than
   silently populate `rank_score=0` for everything and collapse drop order
   to newest-first — covered by
   `test_no_task_is_refused_rather_than_silently_scored_zero`.

## What each function does

- **`should_surface(health) -> ContextTrigger | None`** — pure over
  `compute_health`'s existing output. Fires on high window usage (≥75%) or
  low relevant ratio (≤50%); both thresholds are stated as round numbers
  with no pilot behind them yet, the same honesty `deterministic.py`'s
  `_SUSPICIOUS_DUPLICATE_SHARE` states about itself.
- **`plan_budget(counter, health, ratio=0.15) -> BudgetPlan`** — `basis` is
  never omitted, mirroring invariant 3's rule for `TokenCount`. The default
  ratio (15% of the window) is deliberately conservative: Gloaguen et al.
  (arXiv:2602.11988) found unconditional repository-level context injection
  raised inference cost over 20% with no task-success gain, and a careless
  adaptive-push policy risks reproducing exactly that finding instead of
  avoiding it.
- **`select(candidates, task, plan, ranker) -> SurfaceDecision`** — ranks
  candidates against `task` via the existing `ContextRanker`, then keeps
  what fits `plan.budget` greedily in rank order. Returns items that are
  RANKED, not yet BUDGETED or PROTECTED — that happens only once the caller
  hands the merged list to `ContextPipeline.run`.
- **`memory/surface.py::candidates_for(store, task, counter)`** — converts
  active decisions, hard/soft constraints, discoveries, and unresolved
  failures into `ItemKind.MEMORY` items, which `classify.py:230-231`
  already protects as `CRITICAL` unconditionally. Uses a local content hash
  rather than importing `context/classify.py`'s, since only
  `memory -> context.tokenizer` is a declared dependency edge — adding
  `memory -> context.classify` would be an undeclared one.

## Consequences

- `core/models.py` gains `ContextTrigger`, `BudgetPlan`, `SurfaceDecision`
  — no existing model changed.
- New tests (`test_adaptive.py`, `test_memory_surface.py`) include two that
  run adaptive output through the real `ContextPipeline.run` and assert
  invariant 1 (critical retention) and invariant 2 (ledger chaining) still
  hold post-merge — not new guarantees this module invents, but
  confirmation that it doesn't break the ones that already exist.
- **Not done here, and stated plainly:** this ADR does not wire
  `should_surface`/`select` into `cli/main.py` or `mcp/server.py`, and does
  not run a `verity eval` pilot comparing adaptive-surfacing against a
  no-injection control. Both are natural next steps — the second is
  actually necessary before any claim about this mechanism's effect could
  be published, per invariant 7 (Phase 0) — but they are future work, not
  claimed as complete by adding these three functions.
- The threshold constants (`_HIGH_WINDOW_USAGE`, `_LOW_RELEVANT_RATIO`,
  `_DEFAULT_BUDGET_RATIO`) are placeholders pending exactly that pilot —
  they should not be read as tuned values.

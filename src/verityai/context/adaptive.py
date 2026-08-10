"""A proactive pre-pass: decide what to surface and when, before pruning.

Everything in `prune.py`, `rank.py`, and `classify.py` is reactive -- given
a context and a task, decide what to drop or how to order it. Nothing
computes a *trigger* (is now the moment to push something), a *what-to-add*
(candidates not already in the context), or a *budget policy* (how much
room a push gets, and why). This module is that missing layer, and it is
deliberately thin: every function here is pure, and the one rule that
matters more than any of them is in `select()`'s docstring below.

**Hard rule, non-negotiable (ADR-0025):** this module only ever produces a
`list[ContextItem]` for the caller to hand to the existing
`ContextPipeline.run`, unchanged. It never bypasses that pipeline and never
injects items between its stages. `prune.py`'s ledger chains (invariant 2)
only because `ContextPipeline._stage` is the sole writer of token
accounting; injecting outside that chokepoint corrupts `tokens_before`,
`tokens_after`, and therefore `reduction_ratio` -- the project's headline
number. `context/` must never import `memory/` (CLAUDE.md's dependency
rule), so candidate *sourcing* lives in `memory/surface.py`; this module
only ranks and budgets what it is handed.
"""

from verityai.context.rank import ContextRanker
from verityai.context.tokenizer import TokenCounter
from verityai.core.models import (
    BudgetPlan,
    ContextHealth,
    ContextItem,
    ContextTrigger,
    SurfaceDecision,
)

# Health thresholds that justify a proactive push. Chosen the same way
# `bench/deterministic.py`'s `_SUSPICIOUS_DUPLICATE_SHARE` was: a round
# number that names a real condition worth looking at, not an empirically
# tuned cutoff -- there is no pilot behind these yet (see ADR-0025's stated
# limits).
_HIGH_WINDOW_USAGE = 0.75
_LOW_RELEVANT_RATIO = 0.5

# Default share of the window a push may use. Conservative on purpose: the
# AGENTS.md finding this project's own research review cites (arXiv:2602.11988)
# is that unconditional context injection cost over 20% with no success
# gain -- a budget policy that pushes too eagerly risks reproducing exactly
# that result instead of avoiding it.
_DEFAULT_BUDGET_RATIO = 0.15


def should_surface(health: ContextHealth) -> ContextTrigger | None:
    """Does this context's health justify pushing something into it now?

    Pure over `compute_health`'s existing output -- no new state, no new
    measurement. Returns `None` when nothing in `health` crosses a
    threshold; a caller must not surface anything without a stated reason,
    the same discipline every other degraded/triggered path in this
    codebase follows (invariant 5).
    """
    if health.window_usage >= _HIGH_WINDOW_USAGE:
        return ContextTrigger(
            reason=f"window usage {health.window_usage:.0%} >= {_HIGH_WINDOW_USAGE:.0%}",
            health_snapshot={"window_usage": health.window_usage},
        )
    if health.relevant_ratio <= _LOW_RELEVANT_RATIO:
        return ContextTrigger(
            reason=f"relevant ratio {health.relevant_ratio:.0%} <= {_LOW_RELEVANT_RATIO:.0%}",
            health_snapshot={"relevant_ratio": health.relevant_ratio},
        )
    return None


def no_trigger_reason(health: ContextHealth) -> str:
    """Why `should_surface` declined.

    The negative case needs a reason too. `should_surface` returning `None`
    is itself a decision a user is entitled to see the basis for, and a bare
    "nothing surfaced" reads as "there was nothing to surface" rather than
    "the context did not need it" -- two different claims (invariant 5).
    """
    return (
        f"window usage {health.window_usage:.0%} < {_HIGH_WINDOW_USAGE:.0%} "
        f"and relevant ratio {health.relevant_ratio:.0%} > {_LOW_RELEVANT_RATIO:.0%}"
    )


def plan_budget(
    counter: TokenCounter,
    health: ContextHealth,
    ratio: float = _DEFAULT_BUDGET_RATIO,
) -> BudgetPlan:
    """How many tokens a proactive push gets, and the reasoning behind it.

    `basis` is never omitted -- a budget number without it is exactly the
    bare-int mistake invariant 3 exists to prevent for `TokenCount`, applied
    to a different field.
    """
    window = counter.window
    budget = max(0, int(window * ratio))
    return BudgetPlan(
        budget=budget,
        window=window,
        basis=(
            f"{ratio:.0%} of the {window:,}-token window, deliberately conservative "
            "(Gloaguen et al., arXiv:2602.11988, found unconditional repository-level "
            "context injection raised inference cost >20% with no task-success gain; "
            "see ADR-0025)"
        ),
    )


def select(
    candidates: list[ContextItem],
    task: str,
    plan: BudgetPlan,
    ranker: ContextRanker | None = None,
    trigger: ContextTrigger | None = None,
) -> SurfaceDecision:
    """Rank `candidates` against `task` and keep what fits `plan.budget`.

    `trigger` is carried through onto the returned decision so the record is
    complete where it is built. `SurfaceDecision.trigger` existed from the
    start but nothing populated it, leaving every consumer to patch the field
    after the fact -- which is how a record ends up complete in one caller
    and empty in the next.

    Requires a non-empty `task`: `prune.py`'s `_enforce_budget` sorts by
    `rank_score`, which is only populated when ranking actually ran against
    a task (`ContextPipeline.run` skips the rank stage entirely for an
    empty task). A caller that surfaces items with no task string gets
    `rank_score` defaulting to zero for all of them, which silently
    collapses drop order to newest-first -- this function refuses that
    case outright rather than let it happen invisibly.

    The returned items are RANKED, not yet BUDGETED or PROTECTED: this is a
    pre-pass. The caller must still hand `SurfaceDecision.items` (merged
    with the rest of the context) to `ContextPipeline.run`, which is what
    actually classifies, dedups, and enforces the budget. Selecting here and
    skipping that step would bypass every invariant that pipeline provides.
    """
    if not task:
        return SurfaceDecision(
            items=[],
            plan=plan,
            trigger=trigger,
            degraded_reason=(
                "no task provided -- ranking against nothing would score every "
                "candidate zero and select in an undefined order"
            ),
        )
    if not candidates:
        return SurfaceDecision(items=[], plan=plan, trigger=trigger, degraded_reason=None)

    ranker = ranker or ContextRanker()
    ranking = ranker.rank(task, candidates)

    selected: list[ContextItem] = []
    used = 0
    for scored in ranking.items:
        if used + scored.item.token_count > plan.budget:
            continue
        selected.append(scored.item)
        used += scored.item.token_count

    return SurfaceDecision(
        items=selected,
        plan=plan,
        trigger=trigger,
        degraded_reason=ranking.degraded_reason,
    )

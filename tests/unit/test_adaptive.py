"""Tests for the adaptive context pre-pass (`context/adaptive.py`).

The central question isn't "does ranking work" -- `rank.py` already has its
own tests. It's whether this module's output can be handed to
`ContextPipeline.run` without corrupting anything that pipeline already
guarantees: critical retention still 1.0, the token ledger still chains,
and the two traps ADR-0025 names explicitly (dedup-before-classify eating
an unprotected duplicate, and a missing task collapsing drop order) are
both covered rather than assumed away.
"""

from verityai.context.adaptive import plan_budget, select, should_surface
from verityai.context.prune import ContextPipeline
from verityai.core.models import ContextHealth, ItemKind

from ..conftest import FixedCounter, item


def health(**overrides):
    base = dict(
        window_usage=0.3,
        relevant_ratio=0.9,
        critical_retained=1.0,
        redundancy=0.1,
        tool_noise=0.1,
    )
    base.update(overrides)
    return ContextHealth(**base)


class TestShouldSurface:
    def test_no_trigger_under_normal_health(self):
        assert should_surface(health()) is None

    def test_high_window_usage_triggers_with_a_reason(self):
        trigger = should_surface(health(window_usage=0.9))

        assert trigger is not None
        assert "window usage" in trigger.reason
        assert trigger.health_snapshot["window_usage"] == 0.9

    def test_low_relevant_ratio_triggers_with_a_reason(self):
        trigger = should_surface(health(relevant_ratio=0.2))

        assert trigger is not None
        assert "relevant ratio" in trigger.reason


class TestPlanBudget:
    def test_budget_never_appears_without_its_basis(self):
        plan = plan_budget(FixedCounter(), health())

        assert plan.basis  # never a bare int (invariant 3's spirit)
        assert plan.budget > 0
        assert plan.window == FixedCounter().window

    def test_budget_is_a_fraction_of_the_window(self):
        counter = FixedCounter()
        plan = plan_budget(counter, health(), ratio=0.1)

        assert plan.budget == int(counter.window * 0.1)


class TestSelect:
    def test_no_task_is_refused_rather_than_silently_scored_zero(self):
        """The trap ADR-0025 names explicitly: `_enforce_budget` sorts by
        `rank_score`, populated only when a task was actually ranked
        against. Selecting with no task must refuse, not proceed with an
        undefined order."""
        candidates = [item("must not add Redis", kind=ItemKind.MEMORY)]
        plan = plan_budget(FixedCounter(), health())

        decision = select(candidates, task="", plan=plan)

        assert decision.items == []
        assert decision.degraded_reason is not None
        assert "no task" in decision.degraded_reason

    def test_empty_candidates_is_not_an_error(self):
        plan = plan_budget(FixedCounter(), health())

        decision = select([], task="rate limiting", plan=plan)

        assert decision.items == []
        assert decision.degraded_reason is None

    def test_selection_respects_the_budget(self):
        counter = FixedCounter()
        candidates = [
            item("relevant to rate limiting: use a token bucket", kind=ItemKind.MEMORY, index=0),
            item("relevant to rate limiting: must not add Redis", kind=ItemKind.MEMORY, index=1),
        ]
        for c in candidates:
            measured = counter.count(c.content)
            c.token_count = measured.tokens
            c.token_method = measured.method

        plan = plan_budget(counter, health(), ratio=0.0001)  # forces a tiny budget
        plan.budget = 3  # smaller than either single candidate

        decision = select(candidates, task="rate limiting", plan=plan)

        assert len(decision.items) <= 1
        assert sum(i.token_count for i in decision.items) <= plan.budget


class TestInvariantsSurviveThePipeline:
    """The hard rule (ADR-0025): this module's output is only ever a
    pre-pass into `ContextPipeline.run`, never a bypass of it. These tests
    confirm the pipeline's own invariants hold once adaptive output is
    merged in -- not that adaptive.py has invented new guarantees of its
    own."""

    def test_merged_critical_items_still_retain_at_1_0(self):
        counter = FixedCounter()
        plan = plan_budget(counter, health(), ratio=0.5)
        task = "rate limiting design"
        candidates = [
            item(
                f"constraint for {task}: must not add a Redis dependency",
                kind=ItemKind.MEMORY,
                index=0,
            )
        ]
        for c in candidates:
            measured = counter.count(c.content)
            c.token_count = measured.tokens

        decision = select(candidates, task=task, plan=plan)
        assert decision.items, (
            "fixture must actually rank the candidate in, or the test proves nothing"
        )
        existing = [item(f"some ordinary agent message about {task}", index=1)]

        pipeline = ContextPipeline(counter=counter)
        merged = existing + decision.items
        # A budget far too small for everything, forcing real drop pressure --
        # this only proves something if there's something to drop.
        result = pipeline.run(merged, task=task, budget=1)

        # classify.py:230-231 marks MEMORY items CRITICAL unconditionally --
        # invariant 1 must hold once they're mixed into an otherwise
        # ordinary context and pushed through a budget that can't be met.
        memory_survived = [i for i in result.items if i.kind is ItemKind.MEMORY]
        assert len(memory_survived) == len(decision.items)
        assert result.budget_met is False  # the budget genuinely couldn't be met

    def test_the_token_ledger_still_chains(self):
        """invariant 2: each stage's tokens_before equals the previous
        stage's tokens_after. Adaptive items merged in before `run()` must
        not break that chain -- there is no separate injection path here,
        only a bigger input list."""
        counter = FixedCounter()
        plan = plan_budget(counter, health(), ratio=0.5)
        candidates = [item("a discovery worth surfacing", kind=ItemKind.MEMORY, index=0)]
        for c in candidates:
            c.token_count = counter.count(c.content).tokens

        decision = select(candidates, task="the task", plan=plan)
        merged = [item("an ordinary message", index=1)] + decision.items

        pipeline = ContextPipeline(counter=counter)
        result = pipeline.run(merged, task="the task")

        for prev, cur in zip(result.stages, result.stages[1:], strict=False):
            assert cur.tokens_before == prev.tokens_after


class TestUnscoredCandidatesAreNotLost:
    """`ContextRanker.rank` returns only what it could score -- a candidate
    sharing no term with the task is absent from the result, not present with
    score zero. Selecting from that list alone silently loses exactly the
    record most worth recalling, and then reports it as a budget decision,
    which is worse than losing it quietly."""

    def test_a_candidate_with_no_lexical_overlap_is_still_surfaced(self):
        counter = FixedCounter()
        plan = plan_budget(counter, health(), ratio=0.5)
        overlapping = item("use a token bucket for rate limiting", kind=ItemKind.MEMORY, index=0)
        unrelated = item("must not add a Redis dependency", kind=ItemKind.MEMORY, index=1)
        for c in (overlapping, unrelated):
            c.token_count = counter.count(c.content).tokens

        decision = select([overlapping, unrelated], task="rate limiting", plan=plan)

        contents = [i.content for i in decision.items]
        assert "must not add a Redis dependency" in contents, (
            "a hard constraint that shares no word with the task is the one most "
            "worth recalling; it must not disappear before the budget is applied"
        )
        # Relevance still orders selection -- the scored item comes first.
        assert contents[0] == "use a token bucket for rate limiting"

    def test_the_unrankable_candidates_are_declared(self):
        counter = FixedCounter()
        plan = plan_budget(counter, health(), ratio=0.5)
        unrelated = item("must not add a Redis dependency", kind=ItemKind.MEMORY, index=0)
        unrelated.token_count = counter.count(unrelated.content).tokens

        decision = select([unrelated], task="rate limiting", plan=plan)

        assert decision.degraded_reason is not None
        assert "share no term with the task" in decision.degraded_reason
        assert "rather than dropped" in decision.degraded_reason

    def test_nothing_is_lost_between_candidates_and_the_budget(self):
        """The parts must sum to the whole (invariant 6's principle): every
        candidate is either selected or genuinely did not fit."""
        counter = FixedCounter()
        plan = plan_budget(counter, health(), ratio=0.5)
        candidates = [
            item("use a token bucket for rate limiting", kind=ItemKind.MEMORY, index=0),
            item("must not add a Redis dependency", kind=ItemKind.MEMORY, index=1),
            item("the deploy script lives in ops slash release", kind=ItemKind.MEMORY, index=2),
        ]
        for c in candidates:
            c.token_count = counter.count(c.content).tokens

        decision = select(candidates, task="rate limiting", plan=plan)

        assert len(decision.items) == len(candidates)

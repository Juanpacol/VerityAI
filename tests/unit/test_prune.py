"""Tests for the pruning pipeline.

The first class is the important one. Everything else in this project is a
convenience; "critical context is never dropped" is the promise that makes the
harness safe to put in front of a real agent, and it is asserted here rather
than left to inspection.
"""

import pytest

from verityai.context.health import critical_retention
from verityai.context.prune import MAX_TOOL_OUTPUT_CHARS, ContextPipeline
from verityai.core.models import ContextItem, ItemKind, Relevance

from ..conftest import FixedCounter, item


@pytest.fixture
def pipeline(counter):
    return ContextPipeline(counter=counter)


class TestCriticalItemsSurvive:
    """The pipeline's central safety property."""

    def test_critical_items_survive_an_impossible_budget(self, pipeline):
        items = [
            item("DECISION: use postgres not redis", index=0),
            item("some ordinary discussion " * 50, index=1),
            item("more ordinary discussion " * 50, index=2),
        ]

        result = pipeline.run(items, task="database", budget=1)

        assert critical_retention(result.items, result.items) == 1.0
        surviving = [i.content for i in result.items]
        assert any("DECISION" in c for c in surviving)

    def test_budget_reports_failure_rather_than_dropping_critical(self, pipeline):
        # Every item is critical and the budget cannot possibly be met. The
        # pipeline must go over budget and say so, not start sacrificing.
        items = [item(f"DECISION: choice number {n} " * 20, index=n) for n in range(5)]

        result = pipeline.run(items, budget=10)

        assert len(result.items) == 5
        assert result.budget_met is False
        assert result.tokens_after > 10

    def test_dropped_critical_names_the_overflowing_items(self, pipeline):
        # ADR-0034 regression: dropped_critical used to stay [] always, even
        # when budget_met is False, so a caller had no way to learn which
        # critical items were responsible for the overflow.
        items = [item(f"DECISION: choice number {n} " * 20, index=n) for n in range(5)]

        result = pipeline.run(items, budget=10)

        assert result.budget_met is False
        assert len(result.dropped_critical) == 5
        assert set(result.dropped_critical) == {i.id for i in result.items}

    def test_user_messages_are_protected(self, pipeline):
        items = [
            item("what is the timeout?", kind=ItemKind.USER_MESSAGE, index=0),
            item("filler " * 200, index=1),
        ]

        result = pipeline.run(items, task="timeout", budget=5)

        assert any(i.kind is ItemKind.USER_MESSAGE for i in result.items)

    def test_generic_user_pointers_do_not_starve_the_real_answer(self, pipeline):
        """Regression for the bug ADR-0033 fixes: when most user turns are
        short, generic pointers ("also check X") and the actual answer lives
        in one assistant reply, the pointers must not consume the entire
        critical floor and push the answer out under a tight budget."""
        items = [
            item(f"also check file_{n}.py while we're at it", kind=ItemKind.USER_MESSAGE, index=n)
            for n in range(9)
        ]
        items.append(
            item(
                "the root cause is a missing UNKNOWN case in the unsafe set",
                kind=ItemKind.AGENT_MESSAGE,
                index=9,
            )
        )
        items += [
            item(f"also check file_{n}.py while we're at it", kind=ItemKind.USER_MESSAGE, index=n)
            for n in range(10, 18)
        ]

        result = pipeline.run(
            items, task="what is the root cause of the missing UNKNOWN case", budget=35
        )

        surviving = [i.content for i in result.items]
        assert any("root cause" in c for c in surviving)

    def test_system_prompt_is_protected(self, pipeline):
        items = [
            item("You are a coding agent.", kind=ItemKind.SYSTEM, index=0),
            item("filler " * 200, index=1),
        ]

        result = pipeline.run(items, task="anything", budget=5)

        assert any(i.kind is ItemKind.SYSTEM for i in result.items)


class TestDeduplication:
    def test_exact_duplicates_are_removed(self, pipeline):
        content = "the same file contents repeated verbatim"
        result = pipeline.run([item(content, index=n) for n in range(3)])

        assert len(result.items) == 1

    def test_dedup_ignores_whitespace_differences(self, pipeline):
        result = pipeline.run(
            [
                item("def f():\n    return 1", index=0),
                item("def f():\n\n        return 1", index=1),
            ]
        )

        assert len(result.items) == 1

    def test_first_occurrence_is_the_one_kept(self, pipeline):
        first = item("duplicated", index=0)
        second = item("duplicated", index=1)

        result = pipeline.run([first, second])

        assert result.items[0].id == first.id


class TestCompression:
    def test_oversized_tool_output_is_elided(self, pipeline):
        long_log = "x" * (MAX_TOOL_OUTPUT_CHARS * 3)
        result = pipeline.run([item(long_log, kind=ItemKind.TOOL_OUTPUT, index=0)])

        content = result.items[0].content
        assert len(content) < len(long_log)
        assert "elided by verity" in content

    def test_elision_is_marked_not_silent(self, pipeline):
        """A truncated log that does not announce itself is a correctness bug."""
        long_log = "line\n" * 2000
        result = pipeline.run([item(long_log, kind=ItemKind.TOOL_OUTPUT, index=0)])

        assert result.items[0].metadata["compressed"] is True
        assert result.items[0].metadata["original_chars"] == len(long_log)

    def test_short_tool_output_is_untouched(self, pipeline):
        short = "tests passed: 42 of 42, no failures reported anywhere"
        result = pipeline.run([item(short, kind=ItemKind.TOOL_OUTPUT, index=0)])

        assert result.items[0].content == short

    def test_non_tool_output_is_never_compressed(self, pipeline):
        long_message = "reasoning " * 2000
        result = pipeline.run([item(long_message, kind=ItemKind.AGENT_MESSAGE, index=0)])

        assert result.items[0].content == long_message


class TestTokenAccounting:
    def test_stage_ledger_is_continuous(self, pipeline):
        """Each stage must start where the previous one ended.

        A gap means a stage changed tokens without recording it, which would
        make the headline savings figure unauditable.
        """
        items = [item(f"content number {n} " * 10, index=n) for n in range(20)]
        result = pipeline.run(items, task="content", budget=100)

        # strict=False: pairing a list with its own tail is deliberately
        # uneven -- the last stage has no successor to compare against.
        for earlier, later in zip(result.stages, result.stages[1:], strict=False):
            assert earlier.tokens_after == later.tokens_before
            assert earlier.items_after == later.items_before

    def test_first_and_last_stage_match_the_totals(self, pipeline):
        items = [item(f"content {n} " * 10, index=n) for n in range(10)]
        result = pipeline.run(items, task="content", budget=50)

        assert result.stages[0].tokens_before == result.tokens_before
        assert result.stages[-1].tokens_after == result.tokens_after

    def test_reduction_ratio_of_empty_context_is_zero_not_a_crash(self, pipeline):
        result = pipeline.run([])

        assert result.reduction_ratio == 0.0
        assert result.tokens_saved == 0

    def test_token_method_is_always_reported(self, pipeline):
        result = pipeline.run([item("hello world")])

        assert result.token_method == "fixed:words"
        assert all(i.token_method == "fixed:words" for i in result.items)


class TestPlacement:
    def test_critical_items_are_split_across_both_ends(self):
        pipeline = ContextPipeline(counter=FixedCounter())
        items = [
            item("DECISION: first", index=0),
            item("ordinary one", index=1),
            item("ordinary two", index=2),
            item("DECISION: second", index=3),
        ]

        result = pipeline.run(items)
        contents = [i.content for i in result.items]

        assert "DECISION" in contents[0]
        assert "DECISION" in contents[-1]

    def test_placement_never_loses_items(self, pipeline):
        items = [item(f"unique content {n}", index=n) for n in range(8)]
        result = pipeline.run(items)

        assert len(result.items) == 8

    def test_context_without_critical_items_keeps_original_order(self, pipeline):
        items = [
            ContextItem(
                kind=ItemKind.TOOL_OUTPUT,
                content=f"substantive tool output number {n} with real content in it",
                original_index=n,
            )
            for n in range(4)
        ]
        result = pipeline.run(items)

        assert [i.original_index for i in result.items] == [0, 1, 2, 3]


class TestBudgetDropOrder:
    def test_irrelevant_content_is_dropped_before_relevant(self, pipeline):
        items = [
            item("ok", kind=ItemKind.TOOL_OUTPUT, index=0),
            item("substantive discussion of the caching layer design", index=1),
        ]

        result = pipeline.run(items, task="caching layer", budget=8)

        remaining = [i.content for i in result.items]
        assert any("caching" in c for c in remaining)

    def test_relevance_is_recorded_with_a_reason(self, pipeline):
        result = pipeline.run([item("DECISION: use postgres", index=0)])

        kept = result.items[0]
        assert kept.relevance is Relevance.CRITICAL
        assert "decision:" in kept.relevance_reason

    def test_tied_rank_score_drops_the_oldest_first(self, pipeline):
        # ADR-0034 regression: the tiebreak key used to sort on
        # -original_index, which drops the *newest* tied item first --
        # the opposite of the docstring's "oldest goes before the newest".
        older = item("some filler content here", index=0)
        newer = item("some filler content here", index=5)
        for i in (older, newer):
            i.relevance = Relevance.RELEVANT
            i.metadata["rank_score"] = 0.0
            i.token_count = 3

        result = pipeline._enforce_budget([older, newer], budget=4)

        assert [i.original_index for i in result] == [5]

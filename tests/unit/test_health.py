"""Tests for context health reporting.

The rule under test throughout: the aggregate score never appears without its
components. This is enforced in `render_health`'s output rather than left to
convention, because the pre-pivot confidence score demonstrated exactly how a
lone composite number goes wrong — T1 measured it uncalibrated (ECE 0.14-0.50)
and, in one configuration, inverted.
"""

from verityai.context.classify import classify_all
from verityai.context.health import (
    compute_health,
    critical_retention,
    digit_retention,
    render_health,
)
from verityai.core.models import ContextHealth, ItemKind

from ..conftest import FixedCounter, item


def measured(items, counter=None):
    from verityai.context.prune import ContextPipeline

    pipeline = ContextPipeline(counter=counter or FixedCounter())
    return classify_all([pipeline.measure(i, n) for n, i in enumerate(items)])


class TestRendering:
    def test_the_score_never_appears_alone(self):
        health = ContextHealth(
            window_usage=0.5,
            relevant_ratio=0.8,
            critical_retained=1.0,
            redundancy=0.1,
            tool_noise=0.2,
        )

        rendered = render_health(health)

        for dimension in (
            "Window usage",
            "Relevant context",
            "Critical retained",
            "Redundancy",
            "Tool noise",
            "Stale facts",
            "Contradictions",
        ):
            assert dimension in rendered, dimension
        assert "Health" in rendered

    def test_components_are_printed_before_the_score(self):
        health = ContextHealth(
            window_usage=0.1,
            relevant_ratio=0.9,
            critical_retained=1.0,
            redundancy=0.0,
            tool_noise=0.0,
        )
        rendered = render_health(health)

        assert rendered.index("Redundancy") < rendered.index("Health")

    def test_the_token_method_is_always_shown(self):
        health = ContextHealth(
            window_usage=0.1,
            relevant_ratio=0.9,
            critical_retained=1.0,
            redundancy=0.0,
            tool_noise=0.0,
            token_method="heuristic:chars/4",
        )

        assert "heuristic:chars/4" in render_health(health)

    def test_notes_are_rendered(self):
        health = ContextHealth(
            window_usage=0.1,
            relevant_ratio=0.9,
            critical_retained=1.0,
            redundancy=0.0,
            tool_noise=0.0,
            notes=["something worth knowing"],
        )

        assert "something worth knowing" in render_health(health)


class TestDimensions:
    def test_an_empty_context_reports_rather_than_crashing(self):
        health = compute_health([], counter=FixedCounter())

        assert health.total_tokens == 0
        assert "empty context" in health.notes

    def test_estimated_counts_are_flagged_in_the_notes(self):
        counter = FixedCounter()
        counter._encoder = None

        health = compute_health(measured([item("some content here")]), counter=counter)

        assert any("estimates" in note for note in health.notes)

    def test_redundancy_rises_with_duplicates(self):
        unique = measured([item(f"unique content {n}", index=n) for n in range(4)])
        duplicated = measured([item("identical content", index=n) for n in range(4)])

        assert compute_health(duplicated).redundancy > compute_health(unique).redundancy

    def test_tool_noise_reflects_tool_output_share(self):
        items = measured(
            [
                item("a real discussion of the design", index=0),
                item("verbose build log output " * 20, kind=ItemKind.TOOL_OUTPUT, index=1),
            ]
        )

        assert compute_health(items).tool_noise > 0.5

    def test_window_usage_is_relative_to_the_window(self):
        items = measured([item("word " * 100)])

        small = compute_health(items, window=200)
        large = compute_health(items, window=100_000)

        assert small.window_usage > large.window_usage

    def test_ratios_are_clamped_to_one(self):
        items = measured([item("word " * 500)])

        assert compute_health(items, window=10).window_usage == 1.0

    def test_unclassified_items_are_called_out(self):
        from verityai.core.models import ContextItem

        raw = [ContextItem(kind=ItemKind.AGENT_MESSAGE, content="never classified")]

        health = compute_health(raw, counter=FixedCounter())

        assert any("unclassified" in note for note in health.notes)


class TestCriticalRetention:
    def test_full_retention_is_one(self):
        items = measured([item("DECISION: keep me", index=0)])

        assert critical_retention(items, items) == 1.0

    def test_a_dropped_critical_item_is_detected(self):
        items = measured([item("DECISION: keep me", index=0), item("ordinary", index=1)])
        after = [i for i in items if not i.is_protected]

        assert critical_retention(items, after) < 1.0

    def test_a_context_with_no_critical_items_retains_trivially(self):
        items = measured([item("just ordinary content here", index=0)])
        assert critical_retention(items, []) == 1.0


class TestDigitRetention:
    """Mirrors TestCriticalRetention, but for financial figures specifically."""

    def test_full_retention_is_one(self):
        items = measured([item("Total: $4,231.50", index=0)])

        assert digit_retention(items, items) == 1.0

    def test_a_dropped_figure_is_detected(self):
        items = measured([item("Total: $4,231.50", index=0), item("ordinary", index=1)])
        after = [i for i in items if "$" not in i.content]

        assert digit_retention(items, after) < 1.0

    def test_a_context_with_no_figures_retains_trivially(self):
        items = measured([item("just ordinary content here", index=0)])

        assert digit_retention(items, []) == 1.0

    def test_a_partially_surviving_set_is_a_fraction_not_zero_or_one(self):
        items = measured([item("Amount: $100", index=0), item("Amount: $200", index=1)])
        after = [items[0]]

        assert digit_retention(items, after) == 0.5

    def test_retention_compares_the_figure_not_the_item(self):
        """The same figure surviving in a DIFFERENT item still counts --
        this measures whether the information survived, not whether a
        specific item id did (that is critical_retention's job)."""
        from verityai.core.models import ContextItem, ItemKind

        before = measured([item("Total: $4,231.50", index=0)])
        after = [ContextItem(kind=ItemKind.AGENT_MESSAGE, content="Recap: total was $4,231.50")]

        assert digit_retention(before, after) == 1.0


class TestScore:
    def test_a_healthy_context_scores_high(self):
        health = ContextHealth(
            window_usage=0.2,
            relevant_ratio=1.0,
            critical_retained=1.0,
            redundancy=0.0,
            tool_noise=0.0,
        )

        assert health.score > 0.9

    def test_losing_critical_memory_dominates_the_score(self):
        """Critical retention carries the heaviest weight by design."""
        lost_critical = ContextHealth(
            window_usage=0.0,
            relevant_ratio=1.0,
            critical_retained=0.0,
            redundancy=0.0,
            tool_noise=0.0,
        )
        redundant = ContextHealth(
            window_usage=0.0,
            relevant_ratio=1.0,
            critical_retained=1.0,
            redundancy=1.0,
            tool_noise=0.0,
        )

        assert lost_critical.score < redundant.score

    def test_the_score_stays_within_bounds(self):
        worst = ContextHealth(
            window_usage=1.0,
            relevant_ratio=0.0,
            critical_retained=0.0,
            redundancy=1.0,
            tool_noise=1.0,
        )
        best = ContextHealth(
            window_usage=0.0,
            relevant_ratio=1.0,
            critical_retained=1.0,
            redundancy=0.0,
            tool_noise=0.0,
        )

        assert worst.score == 0.0
        assert best.score == 1.0

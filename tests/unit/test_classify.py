"""Tests for relevance classification.

The classifier is a pile of heuristics, and heuristics are only defensible if
their edge cases are pinned down. The `TestFailureSignals` class in particular
guards against the most costly failure mode available to this system: deciding
that a short error message is noise and deleting the one line that explains
what went wrong.
"""

from verityai.context.classify import classify_all, classify_item, content_hash
from verityai.core.models import ContextItem, ItemKind, Relevance

from ..conftest import item


def classify_one(content, kind=ItemKind.AGENT_MESSAGE, index=0, total=None):
    return classify_item(
        ContextItem(kind=kind, content=content, original_index=index),
        seen_hashes={},
        total_items=total,
    )


class TestFailureSignals:
    """Short does not mean uninformative."""

    def test_exit_code_survives_despite_being_short(self):
        result = classify_one("exit code 1", kind=ItemKind.TOOL_OUTPUT)

        assert result.relevance is not Relevance.IRRELEVANT

    def test_short_error_messages_survive(self):
        for message in ("ERROR: refused", "fatal: not found", "timeout", "panic"):
            result = classify_one(message, kind=ItemKind.TOOL_OUTPUT)
            assert result.relevance is not Relevance.IRRELEVANT, message

    def test_genuinely_empty_output_is_irrelevant(self):
        result = classify_one("ok", kind=ItemKind.TOOL_OUTPUT)

        assert result.relevance is Relevance.IRRELEVANT

    def test_progress_bars_are_irrelevant(self):
        result = classify_one("[42%] ||||||||||||||||||||||||", kind=ItemKind.TOOL_OUTPUT)

        assert result.relevance is Relevance.IRRELEVANT


class TestExplicitMarkers:
    def test_decision_marker_makes_an_item_critical(self):
        result = classify_one("DECISION: store sessions in redis")

        assert result.relevance is Relevance.CRITICAL

    def test_prohibition_language_makes_an_item_critical(self):
        for phrase in ("you must not touch the schema", "never log the token"):
            assert classify_one(phrase).relevance is Relevance.CRITICAL, phrase

    def test_explicit_marker_beats_every_other_rule(self):
        """Explicit intent outranks inference, including obsolescence."""
        result = classify_one("DECISION: this approach is no longer used")

        assert result.relevance is Relevance.CRITICAL

    def test_obsolescence_marker_demotes(self):
        result = classify_one("that plan was superseded by the new one")

        assert result.relevance is Relevance.OBSOLETE

    def test_markers_are_case_insensitive(self):
        assert classify_one("decision: lowercase works").relevance is Relevance.CRITICAL
        assert classify_one("DECISION: uppercase works").relevance is Relevance.CRITICAL


class TestProtectedKinds:
    def test_system_prompts_are_critical(self):
        result = classify_one("You are an agent.", kind=ItemKind.SYSTEM)

        assert result.relevance is Relevance.CRITICAL

    def test_memory_records_are_critical(self):
        result = classify_one("previously recorded fact", kind=ItemKind.MEMORY)

        assert result.relevance is Relevance.CRITICAL

    def test_user_messages_are_critical(self):
        result = classify_one("please add retries", kind=ItemKind.USER_MESSAGE)

        assert result.relevance is Relevance.CRITICAL


class TestDuplicateDetection:
    def test_later_duplicates_are_redundant(self):
        items = [item("identical content here", index=n) for n in range(3)]

        classified = classify_all(items)

        assert classified[0].relevance is not Relevance.REDUNDANT
        assert classified[1].relevance is Relevance.REDUNDANT
        assert classified[2].relevance is Relevance.REDUNDANT

    def test_duplicates_are_reported_against_the_first_occurrence(self):
        classified = classify_all([item("same", index=0), item("same", index=1)])

        assert "item #0" in classified[1].relevance_reason

    def test_hash_normalizes_whitespace(self):
        assert content_hash("a  b\n\nc") == content_hash("a b c")

    def test_hash_distinguishes_different_content(self):
        assert content_hash("alpha") != content_hash("beta")


class TestRecency:
    def test_recent_items_in_a_long_context_are_critical(self):
        items = [item(f"message number {n} with content", index=n) for n in range(30)]

        classified = classify_all(items)

        assert classified[-1].relevance is Relevance.CRITICAL
        assert "recent" in classified[-1].relevance_reason

    def test_recency_does_not_apply_to_short_contexts(self):
        """With few items there is no 'middle' to lose, so nothing is pinned."""
        items = [item(f"message {n} with some content", index=n) for n in range(4)]

        classified = classify_all(items)

        assert not all(c.relevance is Relevance.CRITICAL for c in classified)


class TestReasons:
    def test_every_classified_item_carries_a_reason(self):
        items = [
            item("DECISION: something", index=0),
            item("ordinary content here", index=1),
            item("ok", kind=ItemKind.TOOL_OUTPUT, index=2),
        ]

        for classified in classify_all(items):
            assert classified.relevance_reason, classified.content

    def test_default_reason_names_the_absence_of_a_rule(self):
        result = classify_one("just some ordinary discussion of the code")

        assert result.relevance is Relevance.RELEVANT
        assert "no demotion rule matched" in result.relevance_reason

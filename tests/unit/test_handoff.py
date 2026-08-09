"""Tests for the handoff document.

The document's job is to let a cold session continue the work. That is
ultimately judged by a human (see the verification section of the pivot plan),
but the mechanical preconditions are testable and are tested here: the
sections that must always be present are present, empty sections say they are
empty rather than vanishing, and budget pressure degrades the document in a
declared order rather than truncating it into uselessness.
"""

from verityai.core.models import Constraint, Decision, Discovery, Failure, Task
from verityai.memory.handoff import build_handoff

from ..conftest import FixedCounter

REQUIRED_SECTIONS = (
    "## TASK",
    "## CURRENT STATE",
    "## DECISIONS",
    "## CONSTRAINTS",
    "## DISCOVERIES",
    "## FAILURES",
    "## RELEVANT FILES",
    "## NEXT ACTION",
)


def populate(store):
    store.set_task(
        Task(
            title="add rate limiting",
            description="token bucket per API key",
            next_action="write the middleware test",
            relevant_files=["src/api/rate_limit.py"],
        )
    )
    store.append(Decision(statement="use a token bucket", rationale="bursts are expected"))
    store.append(Constraint(statement="must not add a Redis dependency"))
    store.append(Discovery(statement="the existing middleware runs after auth"))
    store.append(Failure(attempted="a fixed window counter", error="rejected legitimate bursts"))
    return store


class TestStructure:
    def test_all_sections_are_present(self, store):
        document, _ = build_handoff(populate(store))

        for section in REQUIRED_SECTIONS:
            assert section in document, section

    def test_sections_appear_in_the_declared_order(self, store):
        document, _ = build_handoff(populate(store))

        positions = [document.index(section) for section in REQUIRED_SECTIONS]
        assert positions == sorted(positions)

    def test_next_action_is_last(self, store):
        """It is what the reader acts on, so it sits in the strongest tail position."""
        document, _ = build_handoff(populate(store))

        assert document.index("## NEXT ACTION") == max(
            document.index(section) for section in REQUIRED_SECTIONS
        )


class TestEmptyState:
    def test_empty_sections_say_so_rather_than_disappearing(self, store):
        document, _ = build_handoff(store)

        for section in REQUIRED_SECTIONS:
            assert section in document, section
        assert "None recorded." in document

    def test_a_missing_task_produces_actionable_guidance(self, store):
        document, _ = build_handoff(store)

        assert "verity task set" in document

    def test_a_missing_next_action_is_flagged(self, store):
        store.set_task(Task(title="something"))
        document, _ = build_handoff(store)

        assert "decide this before continuing" in document


class TestContent:
    def test_rationale_is_included(self, store):
        document, _ = build_handoff(populate(store))

        assert "bursts are expected" in document

    def test_hard_constraints_are_marked(self, store):
        document, _ = build_handoff(populate(store))

        assert "[HARD]" in document

    def test_soft_constraints_are_distinguished(self, store):
        store.append(Constraint(statement="prefer stdlib", hard=False))
        document, _ = build_handoff(store)

        assert "[soft]" in document

    def test_failures_carry_a_do_not_repeat_instruction(self, store):
        document, _ = build_handoff(populate(store))

        assert "do not repeat" in document
        assert "a fixed window counter" in document

    def test_resolved_failures_are_excluded(self, store):
        store.append(Failure(attempted="already fixed", resolved=True))
        document, _ = build_handoff(store)

        assert "already fixed" not in document


class TestBudget:
    def test_an_unbudgeted_document_is_complete(self, store):
        _, report = build_handoff(populate(store), counter=FixedCounter())

        assert report["dropped_sections"] == []
        assert report["budget_met"] is True

    def test_budget_pressure_drops_sections_in_the_declared_order(self, store):
        _, report = build_handoff(populate(store), budget=40, counter=FixedCounter())

        assert report["dropped_sections"], "expected something to be dropped"
        assert report["dropped_sections"][0] == "discoveries"

    def test_task_and_next_action_survive_any_budget(self, store):
        document, _ = build_handoff(populate(store), budget=1, counter=FixedCounter())

        assert "## TASK" in document
        assert "## NEXT ACTION" in document
        assert "add rate limiting" in document
        assert "write the middleware test" in document

    def test_the_report_names_what_was_dropped(self, store):
        _, report = build_handoff(populate(store), budget=30, counter=FixedCounter())

        assert set(report["dropped_sections"]).issubset(
            {"discoveries", "decisions_rationale", "failures", "constraints", "decisions"}
        )

    def test_token_method_is_always_reported(self, store):
        _, report = build_handoff(populate(store), counter=FixedCounter())

        assert report["token_method"] == "fixed:words"

    def test_an_impossible_budget_reports_failure_rather_than_lying(self, store):
        _, report = build_handoff(populate(store), budget=1, counter=FixedCounter())

        assert report["budget_met"] is False

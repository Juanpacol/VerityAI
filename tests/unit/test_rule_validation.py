"""Unit tests for the Z3 rule-consistency gate + human approval queue
(agent/rule_validation.py)."""

import pytest

from verityai.agent.rule_validation import (
    ApprovalDecision,
    RuleApprovalQueue,
    RuleValidationStatus,
    validate_candidate_rule,
)
from verityai.ontology.models import Rule


def make_rule(test_code=None, **overrides) -> Rule:
    defaults = dict(
        name="learned_rule",
        description="a candidate rule",
        category="learned",
        condition="some condition",
        severity="medium",
        applies_to=["python"],
        test_code=test_code,
    )
    defaults.update(overrides)
    return Rule(**defaults)


class TestValidateCandidateRule:
    def test_no_test_code_is_unverifiable(self):
        rule = make_rule(test_code=None)
        result = validate_candidate_rule(rule)

        assert result.status == RuleValidationStatus.UNVERIFIABLE
        assert "no test_code" in result.reason.lower()

    def test_consistent_test_code_is_consistent(self):
        rule = make_rule(test_code="x = 1\nassert x == 1")
        result = validate_candidate_rule(rule)

        assert result.status == RuleValidationStatus.CONSISTENT

    def test_contradictory_test_code_is_contradictory(self):
        rule = make_rule(test_code="x = 1\nassert x == 2")
        result = validate_candidate_rule(rule)

        assert result.status == RuleValidationStatus.CONTRADICTORY

    def test_non_verifiable_construct_is_unverifiable(self):
        rule = make_rule(test_code="result = some_undefined_helper()")
        result = validate_candidate_rule(rule)

        assert result.status == RuleValidationStatus.UNVERIFIABLE


class TestRuleApprovalQueue:
    def test_consistent_rule_starts_pending(self):
        rule = make_rule(test_code="x = 1\nassert x == 1")
        validation = validate_candidate_rule(rule)
        queue = RuleApprovalQueue()

        entry = queue.submit(rule, validation)

        assert entry.decision == ApprovalDecision.PENDING
        assert queue.pending() == [entry]

    def test_contradictory_rule_is_auto_rejected(self):
        rule = make_rule(test_code="x = 1\nassert x == 2")
        validation = validate_candidate_rule(rule)
        queue = RuleApprovalQueue()

        entry = queue.submit(rule, validation)

        assert entry.decision == ApprovalDecision.REJECTED
        assert queue.pending() == []

    def test_approve_moves_rule_to_approved_unapplied(self):
        rule = make_rule(test_code="x = 1\nassert x == 1")
        validation = validate_candidate_rule(rule)
        queue = RuleApprovalQueue()
        queue.submit(rule, validation)

        queue.approve(rule.id, reviewer_note="looks fine")

        assert queue.pending() == []
        assert [e.rule.id for e in queue.approved_unapplied()] == [rule.id]

    def test_reject_keeps_rule_out_of_approved(self):
        rule = make_rule(test_code="x = 1\nassert x == 1")
        validation = validate_candidate_rule(rule)
        queue = RuleApprovalQueue()
        queue.submit(rule, validation)

        queue.reject(rule.id, reviewer_note="not useful")

        assert queue.approved_unapplied() == []

    def test_approve_can_override_an_auto_rejection(self):
        rule = make_rule(test_code="x = 1\nassert x == 2")
        validation = validate_candidate_rule(rule)
        queue = RuleApprovalQueue()
        queue.submit(rule, validation)

        queue.approve(rule.id, reviewer_note="test_code was wrong, rule itself is fine")

        assert [e.rule.id for e in queue.approved_unapplied()] == [rule.id]

    def test_mark_applied_removes_from_approved_unapplied(self):
        rule = make_rule(test_code="x = 1\nassert x == 1")
        validation = validate_candidate_rule(rule)
        queue = RuleApprovalQueue()
        queue.submit(rule, validation)
        queue.approve(rule.id)

        queue.mark_applied(rule.id)

        assert queue.approved_unapplied() == []

    def test_operating_on_unknown_rule_id_raises(self):
        queue = RuleApprovalQueue()
        with pytest.raises(KeyError):
            queue.approve(make_rule().id)

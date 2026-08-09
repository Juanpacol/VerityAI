"""Tests for the rescued rule engine.

`TestCheckForViolation` is the one that matters most: it pins down the exact
bug T6 found and fixed. `apply_rule_to_code` is a positive-derivation checker
that can structurally never return FAIL, so a rule whose precondition names a
dangerous pattern gets reported PASS on genuinely vulnerable code.
`check_for_violation` is the corrected inversion, and this file exists so that
correction survives being moved out of quarantine unmodified.
"""

from verityai.core.models import Rule, VerificationStatus
from verityai.reliability.rule_engine import RuleEngine

RACE_RULE = Rule(
    id="no-check-then-act-race",
    name="No Check-Then-Act Race",
    formal_spec="PRE: check_then_act_on_shared_resource; POST: check_and_act_combined_atomically",
    category="security",
)


class TestCheckForViolation:
    """The T6 correction: PRE is the danger trigger, POST is the required mitigation."""

    def test_trigger_present_mitigation_absent_is_a_violation(self):
        engine = RuleEngine()

        status, message = engine.check_for_violation(
            RACE_RULE, {"check_then_act_on_shared_resource": True}
        )

        assert status is VerificationStatus.FAIL
        assert "violated" in message

    def test_trigger_present_mitigation_present_passes(self):
        engine = RuleEngine()

        status, _ = engine.check_for_violation(
            RACE_RULE,
            {"check_then_act_on_shared_resource": True, "check_and_act_combined_atomically": True},
        )

        assert status is VerificationStatus.PASS

    def test_trigger_absent_is_unknown_not_pass(self):
        """The rule simply doesn't apply here -- that is not the same as safe."""
        engine = RuleEngine()

        status, _ = engine.check_for_violation(RACE_RULE, {"unrelated_fact": True})

        assert status is VerificationStatus.UNKNOWN

    def test_a_rule_with_no_formal_spec_is_unknown(self):
        engine = RuleEngine()
        bare_rule = Rule(id="x", name="x", formal_spec="")

        status, _ = engine.check_for_violation(bare_rule, {"anything": True})

        assert status is VerificationStatus.UNKNOWN

    def test_this_can_return_fail_unlike_apply_rule_to_code(self):
        """The bug being fixed: apply_rule_to_code structurally cannot fail."""
        engine = RuleEngine()
        facts = {"check_then_act_on_shared_resource": True}

        derivation_status, _ = engine.apply_rule_to_code(RACE_RULE, facts)
        violation_status, _ = engine.check_for_violation(RACE_RULE, facts)

        assert derivation_status is not VerificationStatus.FAIL
        assert violation_status is VerificationStatus.FAIL


class TestApplyRuleToCode:
    """The original derivation method -- can only ever return PASS or UNKNOWN."""

    def test_preconditions_met_derives_and_passes(self):
        engine = RuleEngine()

        status, message = engine.apply_rule_to_code(
            RACE_RULE, {"check_then_act_on_shared_resource": True}
        )

        assert status is VerificationStatus.PASS
        assert "applied" in message

    def test_preconditions_not_met_is_unknown(self):
        engine = RuleEngine()

        status, _ = engine.apply_rule_to_code(RACE_RULE, {"unrelated": True})

        assert status is VerificationStatus.UNKNOWN

    def test_never_returns_fail(self):
        """No input to this method can produce FAIL -- that is the bug T6 found."""
        engine = RuleEngine()

        for facts in ({}, {"check_then_act_on_shared_resource": True}, {"anything": True}):
            status, _ = engine.apply_rule_to_code(RACE_RULE, facts)
            assert status is not VerificationStatus.FAIL


class TestForwardChaining:
    def test_infer_reaches_a_fixed_point(self):
        engine = RuleEngine()
        engine.add_rule(Rule(id="a", name="a", formal_spec="PRE: x; POST: y"))
        engine.add_rule(Rule(id="b", name="b", formal_spec="PRE: y; POST: z"))

        facts, trace = engine.infer({"x"})

        assert facts == {"x", "y", "z"}
        assert len(trace) == 2

    def test_infer_stops_at_max_iterations(self):
        engine = RuleEngine(max_iterations=1)
        engine.add_rule(Rule(id="a", name="a", formal_spec="PRE: x; POST: y"))
        engine.add_rule(Rule(id="b", name="b", formal_spec="PRE: y; POST: z"))

        facts, _ = engine.infer({"x"})

        assert "z" not in facts

    def test_no_applicable_rule_derives_nothing_new(self):
        engine = RuleEngine()
        engine.add_rule(Rule(id="a", name="a", formal_spec="PRE: never_present; POST: y"))

        facts, trace = engine.infer({"x"})

        assert facts == {"x"}
        assert trace == []

    def test_reset_clears_state(self):
        engine = RuleEngine()
        engine.add_fact("something")

        engine.reset()

        assert engine.facts == set()
        assert engine.inference_trace == []


class TestApplicableRules:
    def test_filters_by_language(self):
        engine = RuleEngine()
        py_rule = Rule(id="a", name="a", formal_spec="PRE: x; POST: y", applies_to=["python"])
        js_rule = Rule(id="b", name="b", formal_spec="PRE: x; POST: y", applies_to=["javascript"])
        engine.add_rules_batch([py_rule, js_rule])

        applicable = engine.get_applicable_rules({"x": True}, language="python")

        assert applicable == [py_rule]

    def test_filters_by_precondition(self):
        engine = RuleEngine()
        engine.add_rule(Rule(id="a", name="a", formal_spec="PRE: x; POST: y"))

        assert engine.get_applicable_rules({"other": True}) == []

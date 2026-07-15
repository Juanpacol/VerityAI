"""Unit tests for rule engine (deductive reasoning)."""

from verityai.ontology.models import Rule, VerificationStatus
from verityai.symbolic.rule_engine import RuleEngine


class TestRuleEngine:
    """Tests for RuleEngine."""

    def test_engine_creation(self):
        """Test RuleEngine initialization."""
        engine = RuleEngine(max_iterations=10)
        assert engine.max_iterations == 10
        assert len(engine.rules) == 0
        assert len(engine.facts) == 0

    def test_add_single_rule(self):
        """Test adding a rule."""
        engine = RuleEngine()
        rule = Rule(
            name="test_rule",
            description="Test rule",
            category="test",
            condition="Test condition",
            severity="low",
            formal_spec="PRE: fact1; POST: fact2",
            applies_to=["python"],
        )

        engine.add_rule(rule)
        assert len(engine.rules) == 1

    def test_add_rules_batch(self):
        """Test adding multiple rules."""
        engine = RuleEngine()
        rules = [
            Rule(
                name=f"rule_{i}",
                description=f"Rule {i}",
                category="test",
                condition=f"Condition {i}",
                severity="low",
                formal_spec=f"PRE: fact{i}; POST: result{i}",
                applies_to=["python"],
            )
            for i in range(3)
        ]

        engine.add_rules_batch(rules)
        assert len(engine.rules) == 3

    def test_add_fact(self):
        """Test adding a fact."""
        engine = RuleEngine()
        engine.add_fact("code_is_sorted")
        assert "code_is_sorted" in engine.facts

    def test_simple_forward_chain(self):
        """Test simple forward chaining: rule application."""
        engine = RuleEngine()

        rule = Rule(
            name="bounds_rule",
            description="Bounds check implies safe",
            category="safety",
            condition="Bounds are checked",
            severity="high",
            formal_spec="PRE: code_has_bounds_check; POST: code_is_safe",
            applies_to=["python"],
        )
        engine.add_rule(rule)

        initial_facts = {"code_has_bounds_check"}
        derived_facts, trace = engine.infer(initial_facts)

        assert "code_is_safe" in derived_facts
        assert len(trace) > 0

    def test_chained_rules(self):
        """Test multiple rules chaining (transitivity)."""
        engine = RuleEngine()

        rule1 = Rule(
            name="rule1",
            description="A implies B",
            category="test",
            condition="A is true",
            severity="low",
            formal_spec="PRE: A; POST: B",
            applies_to=["python"],
        )

        rule2 = Rule(
            name="rule2",
            description="B implies C",
            category="test",
            condition="B is true",
            severity="low",
            formal_spec="PRE: B; POST: C",
            applies_to=["python"],
        )

        engine.add_rules_batch([rule1, rule2])

        initial_facts = {"A"}
        derived_facts, trace = engine.infer(initial_facts)

        assert "B" in derived_facts
        assert "C" in derived_facts
        assert len(trace) == 2

    def test_reset_state(self):
        """Test resetting engine state."""
        engine = RuleEngine()
        engine.add_fact("some_fact")
        engine.inference_trace.append({"rule": "test"})

        engine.reset()

        assert len(engine.facts) == 0
        assert len(engine.inference_trace) == 0

    def test_apply_rule_to_code(self):
        """Test applying a rule to code facts."""
        engine = RuleEngine()

        rule = Rule(
            name="null_check_rule",
            description="Requires null check",
            category="safety",
            condition="Null checks are required",
            severity="high",
            formal_spec="PRE: has_null_check; POST: safe_null_handling",
            applies_to=["python"],
        )
        engine.add_rule(rule)

        code_facts = {"has_null_check": True, "is_verified": False}

        status, explanation = engine.apply_rule_to_code(rule, code_facts)

        assert status == VerificationStatus.PASS
        assert "safe_null_handling" in explanation

    def test_preconditions_not_met(self):
        """Test rule with unmet preconditions."""
        engine = RuleEngine()

        rule = Rule(
            name="test_rule",
            description="Test",
            category="test",
            condition="Test condition",
            severity="low",
            formal_spec="PRE: missing_fact; POST: result",
            applies_to=["python"],
        )
        engine.add_rule(rule)

        code_facts = {"different_fact": True}

        status, explanation = engine.apply_rule_to_code(rule, code_facts)

        assert status == VerificationStatus.UNKNOWN
        assert "preconditions not met" in explanation

    def test_get_applicable_rules(self):
        """Test filtering applicable rules by language and preconditions."""
        engine = RuleEngine()

        python_rule = Rule(
            name="python_rule",
            description="Python specific",
            category="test",
            condition="Python condition",
            severity="low",
            formal_spec="PRE: has_check; POST: result",
            applies_to=["python"],
        )

        java_rule = Rule(
            name="java_rule",
            description="Java specific",
            category="test",
            condition="Java condition",
            severity="low",
            formal_spec="PRE: has_check; POST: result",
            applies_to=["java"],
        )

        engine.add_rules_batch([python_rule, java_rule])

        code_facts = {"has_check": True}

        applicable = engine.get_applicable_rules(code_facts, language="python")

        assert len(applicable) == 1
        assert applicable[0].name == "python_rule"

    def test_get_inference_trace(self):
        """Test retrieving inference trace."""
        engine = RuleEngine()

        rule = Rule(
            name="test_rule",
            description="Test",
            category="test",
            condition="Test condition",
            severity="low",
            formal_spec="PRE: A; POST: B",
            applies_to=["python"],
        )
        engine.add_rule(rule)

        initial_facts = {"A"}
        engine.infer(initial_facts)

        trace = engine.get_inference_trace()

        assert len(trace) > 0
        assert trace[0]["rule"] == "test_rule"
        assert trace[0]["derived"] == "B"


def _violation_rule() -> Rule:
    return Rule(
        name="No Check-Then-Act Race",
        description="Flags an unguarded check-then-act on shared state",
        category="concurrency",
        condition="Shared state mutated without a lock",
        severity="high",
        formal_spec="PRE: check_then_act_on_shared_resource; POST: check_and_act_combined_atomically",
        applies_to=["python"],
    )


class TestCheckForViolation:
    """T6's fix for a real design gap found while prototyping SQLi/race-
    condition pattern matching: `apply_rule_to_code` can only ever return
    PASS or UNKNOWN (there is no code path in it that returns FAIL), so it
    reports a false PASS when fed a vulnerable snippet's own facts against
    a rule whose PRE names the dangerous pattern. `check_for_violation` is
    the new, additive method with the inverse (correct, for this use case)
    framing.
    """

    def test_apply_rule_to_code_regression_documents_the_bug(self):
        """Not a bug to fix here -- `apply_rule_to_code`'s derivation
        semantics are correct for the IBM NSTK forward-chaining use case
        it was built for. This test documents, rather than asserts away,
        the surprising consequence of reusing it for violation-flagging:
        precondition met -> PASS, even though the precondition IS the
        dangerous pattern.
        """
        engine = RuleEngine()
        rule = _violation_rule()
        vulnerable_facts = {"check_then_act_on_shared_resource": True}

        status, _ = engine.apply_rule_to_code(rule, vulnerable_facts)

        assert status == VerificationStatus.PASS  # misleading -- see check_for_violation

    def test_violation_present_and_unmitigated_is_fail(self):
        engine = RuleEngine()
        rule = _violation_rule()
        vulnerable_facts = {"check_then_act_on_shared_resource": True}

        status, explanation = engine.check_for_violation(rule, vulnerable_facts)

        assert status == VerificationStatus.FAIL
        assert "violated" in explanation

    def test_violation_present_but_mitigated_is_pass(self):
        engine = RuleEngine()
        rule = _violation_rule()
        mitigated_facts = {
            "check_then_act_on_shared_resource": True,
            "check_and_act_combined_atomically": True,
        }

        status, explanation = engine.check_for_violation(rule, mitigated_facts)

        assert status == VerificationStatus.PASS
        assert "mitigated" in explanation

    def test_precondition_absent_is_unknown_not_a_false_pass(self):
        """Clean code with no trigger for this specific pattern reports
        UNKNOWN, never an affirmative PASS -- check_for_violation only ever
        claims "a violation was found," never "this code is proven safe."
        """
        engine = RuleEngine()
        rule = _violation_rule()

        status, explanation = engine.check_for_violation(rule, {})

        assert status == VerificationStatus.UNKNOWN
        assert "not present" in explanation

    def test_rule_without_formal_spec_is_unknown(self):
        engine = RuleEngine()
        rule = Rule(
            name="no_spec_rule",
            description="No formal spec",
            category="test",
            condition="n/a",
            severity="low",
            applies_to=["python"],
        )

        status, explanation = engine.check_for_violation(rule, {"anything": True})

        assert status == VerificationStatus.UNKNOWN
        assert "no PRE/POST formal_spec" in explanation

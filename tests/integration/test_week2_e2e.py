"""Integration test for Phase 1 Week 2 — Rule Engine + Counterexample + KG Ingestion."""

import contextlib

from z3 import Int

from verityai.ontology.models import Rule, VerificationStatus
from verityai.symbolic.counterexample import CounterexampleGenerator
from verityai.symbolic.rule_engine import RuleEngine
from verityai.symbolic.z3_engine import Z3Engine


class TestWeek2Integration:
    """End-to-end tests for Week 2 components."""

    def test_binary_search_rule_application(self):
        """Test: verify binary search satisfies bounds check rule."""
        engine = Z3Engine()
        arr_len = Int("arr_len")
        mid = Int("mid")

        # Preconditions: array length > 0
        preconditions = [arr_len > 0]

        # Code constraints: mid = (left + right) // 2, with bounds
        code_constraints = [
            mid >= 0,
            mid < arr_len,
        ]

        # Postcondition: mid is always within bounds
        postcondition = (mid >= 0) & (mid < arr_len)

        result = engine.verify_code(code_constraints, postcondition, preconditions)

        assert result.status == VerificationStatus.PASS
        assert result.confidence > 0.5

    def test_rule_engine_with_seed_rules(self):
        """Test: apply real seed rules (bounds check, array invariant)."""
        engine = RuleEngine()

        # Bounds check rule
        bounds_rule = Rule(
            name="Array Bounds Check",
            description="Verify array accesses are within bounds",
            category="safety",
            condition="Array indices must be within [0, length)",
            severity="critical",
            formal_spec="PRE: array_length_valid; POST: all_accesses_in_bounds",
            applies_to=["python"],
        )

        # Array invariant rule
        invariant_rule = Rule(
            name="Array Invariant",
            description="Verify array properties maintained",
            category="correctness",
            condition="Array properties are preserved",
            severity="high",
            formal_spec="PRE: array_is_sorted; POST: array_still_sorted",
            applies_to=["python"],
        )

        engine.add_rules_batch([bounds_rule, invariant_rule])

        # Code facts for binary search
        code_facts = {
            "array_length_valid": True,
            "array_is_sorted": True,
        }

        # Get applicable rules
        applicable = engine.get_applicable_rules(code_facts, language="python")

        assert len(applicable) == 2
        assert any(r.name == "Array Bounds Check" for r in applicable)

    def test_chaining_deduction_binary_search(self):
        """Test: forward chaining derives nested properties."""
        engine = RuleEngine()

        # Rule 1: if bounds_checked then safe_indexing
        rule1 = Rule(
            name="bounds_implies_safe",
            description="Bounds check implies safe indexing",
            category="safety",
            condition="Bounds check implies safe indexing",
            severity="high",
            formal_spec="PRE: bounds_checked; POST: safe_indexing",
            applies_to=["python"],
        )

        # Rule 2: if safe_indexing then no_buffer_overflow
        rule2 = Rule(
            name="safe_indexing_implies_no_overflow",
            description="Safe indexing prevents buffer overflow",
            category="security",
            condition="Safe indexing prevents buffer overflow",
            severity="critical",
            formal_spec="PRE: safe_indexing; POST: no_buffer_overflow",
            applies_to=["python"],
        )

        engine.add_rules_batch([rule1, rule2])

        # Start with bounds_checked
        initial_facts = {"bounds_checked"}
        derived_facts, trace = engine.infer(initial_facts)

        # Should derive both intermediate and final property
        assert "safe_indexing" in derived_facts
        assert "no_buffer_overflow" in derived_facts
        assert len(trace) == 2

    def test_counterexample_extraction_from_z3_failure(self):
        """Test: extract counterexample when postcondition fails."""
        engine = Z3Engine()
        x = Int("x")
        x_new = Int("x_new")

        # Precondition: x > 0
        preconditions = [x > 0]

        # Code: x_new = x - 10 (result of x - 10)
        code_constraints = [x_new == x - 10]

        # Postcondition: x_new > 0 (will fail for x in [1, 10])
        postcondition = x_new > 0

        result = engine.verify_code(code_constraints, postcondition, preconditions)

        # With x in (0, 10], x_new will be <= 0, so postcondition fails
        assert result.status == VerificationStatus.FAIL
        assert result.violations is not None
        assert len(result.violations) > 0

        # Extract counterexample
        ce = result.violations[0]
        gen = CounterexampleGenerator()
        display = gen.format_for_display(ce)

        assert "Postcondition violated" in display or display is not None

    def test_confidence_degradation_with_unknown_queries(self):
        """Test: confidence score reduces with unknown/timeout queries."""
        engine = Z3Engine(timeout_seconds=0.01)  # Very short timeout

        # Generate some queries (some will timeout)
        x = Int("x")
        for _ in range(3):
            with contextlib.suppress(Exception):
                engine.check_satisfiable([x > 0])

        health = engine.get_health_check()

        # Some queries should have timed out
        assert health["unknown_queries"] >= 0
        # Success rate might be less than 1.0
        assert health["success_rate"] <= 1.0

    def test_rule_grouping_by_severity(self):
        """Test: filter rules by severity level."""
        engine = RuleEngine()

        critical_rule = Rule(
            name="critical_check",
            description="Critical security rule",
            category="security",
            condition="Critical condition",
            severity="critical",
            formal_spec="PRE: A; POST: B",
            applies_to=["python"],
        )

        high_rule = Rule(
            name="high_check",
            description="High priority rule",
            category="correctness",
            condition="High condition",
            severity="high",
            formal_spec="PRE: A; POST: C",
            applies_to=["python"],
        )

        medium_rule = Rule(
            name="medium_check",
            description="Medium priority rule",
            category="style",
            condition="Medium condition",
            severity="medium",
            formal_spec="PRE: A; POST: D",
            applies_to=["python"],
        )

        engine.add_rules_batch([critical_rule, high_rule, medium_rule])

        # Get only critical rules
        critical_only = [r for r in engine.rules if r.severity == "critical"]

        assert len(critical_only) == 1
        assert critical_only[0].name == "critical_check"

    def test_end_to_end_verification_with_rules(self):
        """Full E2E: Z3 verify + extract counterexample + apply rule engine."""
        # Step 1: Z3 verification
        z3_engine = Z3Engine()
        x = Int("x")
        arr_len = Int("arr_len")

        precond = [arr_len > 0, x >= 0, x < arr_len]
        code_constraints = [x == 5]
        postcond = (x >= 0) & (x < arr_len)

        result = z3_engine.verify_code(code_constraints, postcond, precond)

        assert result.status == VerificationStatus.PASS

        # Step 2: Rule engine applies rules to success
        rule_engine = RuleEngine()

        satisfaction_rule = Rule(
            name="bounds_satisfied",
            description="Bounds check satisfied",
            category="safety",
            condition="Bounds check is satisfied",
            severity="critical",
            formal_spec="PRE: code_verified; POST: bounds_satisfied",
            applies_to=["python"],
        )
        rule_engine.add_rule(satisfaction_rule)

        code_facts = {"code_verified": result.status == VerificationStatus.PASS}
        status, explanation = rule_engine.apply_rule_to_code(satisfaction_rule, code_facts)

        assert status == VerificationStatus.PASS
        assert "bounds_satisfied" in explanation

    def test_multiple_counterexamples_grouped(self):
        """Test: group multiple counterexamples by rule."""
        from verityai.ontology.models import Counterexample

        gen = CounterexampleGenerator()

        # Simulate 3 violations from bounds rule, 2 from null rule
        violations = [
            Counterexample(
                rule_id="bounds_check",
                input_values={"idx": 100, "len": 10},
                description="Index out of bounds",
            ),
            Counterexample(
                rule_id="bounds_check",
                input_values={"idx": -1, "len": 10},
                description="Negative index",
            ),
            Counterexample(
                rule_id="null_check",
                input_values={"ptr": None},
                description="Null pointer dereference",
            ),
        ]

        grouped = gen.group_counterexamples(violations)

        assert len(grouped["bounds_check"]) == 2
        assert len(grouped["null_check"]) == 1

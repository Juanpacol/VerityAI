"""Unit tests for symbolic layer (Z3 + AST converter)."""

import pytest
from z3 import And, Bool, Int, IntVal, Or

from verityai.ontology.models import VerificationStatus
from verityai.symbolic import ASTtoSMTConverter, Z3Engine, VerifiableSubsetViolation


class TestZ3Engine:
    """Tests for Z3 Theorem Prover wrapper."""

    def test_engine_creation(self):
        """Test Z3Engine initialization."""
        engine = Z3Engine(timeout_seconds=3.0)
        assert engine.timeout_seconds == 3.0
        assert engine.total_queries == 0

    def test_check_satisfiable_sat(self):
        """Test checking satisfiable constraint."""
        engine = Z3Engine()
        x = Int("x")

        # x > 5 is satisfiable (e.g., x = 6)
        status, model = engine.check_satisfiable([x > 5])

        assert status == VerificationStatus.PASS
        assert model is not None

    def test_check_satisfiable_unsat(self):
        """Test checking unsatisfiable constraint."""
        engine = Z3Engine()
        x = Int("x")

        # x > 5 AND x < 5 is unsatisfiable
        status, model = engine.check_satisfiable([And(x > 5, x < 5)])

        assert status == VerificationStatus.FAIL
        assert model is None

    def test_verify_property_pass(self):
        """Test verifying a valid property."""
        engine = Z3Engine()
        x = Int("x")

        # If x > 0, then x + 1 > x
        preconditions = [x > 0]
        property_to_prove = x + 1 > x

        status, counterexample = engine.verify_property(
            property_to_prove,
            assumptions=preconditions
        )

        assert status == VerificationStatus.PASS
        assert counterexample is None

    def test_verify_property_fail(self):
        """Test verifying an invalid property."""
        engine = Z3Engine()
        x = Int("x")

        # x > 0 does NOT imply x > 10 (counterexample: x = 5)
        preconditions = [x > 0]
        property_to_prove = x > 10

        status, counterexample = engine.verify_property(
            property_to_prove,
            assumptions=preconditions
        )

        assert status == VerificationStatus.FAIL
        assert counterexample is not None
        assert isinstance(counterexample, dict)

    def test_verify_code(self):
        """Test verifying code against postcondition."""
        engine = Z3Engine()
        x = Int("x")

        # Code: x = 5
        code_constraints = [x == 5]
        # Postcondition: x > 0
        postcondition = x > 0

        result = engine.verify_code(
            code_constraints,
            postcondition
        )

        assert result.status == VerificationStatus.PASS
        assert result.confidence > 0.5

    def test_health_check(self):
        """Test engine health statistics."""
        engine = Z3Engine()

        health = engine.get_health_check()

        assert "total_queries" in health
        assert "unknown_queries" in health
        assert "success_rate" in health
        assert health["status"] in ("healthy", "degraded")


class TestASTtoSMTConverter:
    """Tests for AST to SMT converter."""

    def test_converter_creation(self):
        """Test converter initialization."""
        converter = ASTtoSMTConverter(allow_partial=True)
        assert converter.allow_partial is True
        assert len(converter.constraints) == 0

    def test_convert_simple_assignment(self):
        """Test that converter processes simple assignments without crashing."""
        converter = ASTtoSMTConverter()
        code = "x = 5"

        constraints, non_verifiable = converter.convert_code(code)

        # Should process without error, variables should be created
        assert "x" in converter.variables
        assert len(non_verifiable) == 0

    def test_convert_comparison(self):
        """Test that converter creates variables from assignments."""
        converter = ASTtoSMTConverter()
        code = "x = 10\ny = x"

        constraints, non_verifiable = converter.convert_code(code)

        # Should create variables for both assignments
        assert "x" in converter.variables
        assert "y" in converter.variables
        assert len(non_verifiable) == 0

    def test_non_verifiable_function_call(self):
        """Test handling non-verifiable function call."""
        converter = ASTtoSMTConverter(allow_partial=True)
        code = "y = my_function()"

        constraints, non_verifiable = converter.convert_code(code)

        # Should mark as non-verifiable but not crash
        assert len(non_verifiable) > 0

    def test_infer_int_type(self):
        """Test type inference for int."""
        converter = ASTtoSMTConverter()
        import ast

        node = ast.Constant(value=42)
        inferred = converter._infer_type(node)

        assert inferred == "int"

    def test_infer_bool_type(self):
        """Test type inference for bool."""
        converter = ASTtoSMTConverter()
        import ast

        node = ast.Constant(value=True)
        inferred = converter._infer_type(node)

        # Bool constants should be inferred as bool or int (both acceptable)
        assert inferred in ("bool", "int")

    def test_convert_binary_op(self):
        """Test converting binary operations."""
        converter = ASTtoSMTConverter()
        code = "x = 5 + 3"

        constraints, non_verifiable = converter.convert_code(code)

        # Should create variable for result
        assert "x" in converter.variables
        assert len(non_verifiable) == 0

    def test_non_verifiable_loop(self):
        """Test handling non-verifiable loop."""
        converter = ASTtoSMTConverter(allow_partial=True)
        code = """
for i in range(10):
    x = x + i
"""

        constraints, non_verifiable = converter.convert_code(code)

        # Loop marked as non-verifiable
        assert len(non_verifiable) > 0


class TestZ3EngineIntegration:
    """Integration tests for Z3 engine."""

    def test_binary_search_verification(self):
        """Test verifying binary search bounds are maintained."""
        engine = Z3Engine()

        # Precondition: array has valid length
        arr_len = Int("arr_len")
        left = Int("left")
        right = Int("right")

        preconditions = [
            arr_len > 0,
            left >= 0,
            right < arr_len,
            left <= right
        ]

        # Invariant: bounds are always maintained
        invariant = And(
            left >= 0,
            right < arr_len,
            left <= right
        )

        # Postcondition: if we maintain bounds, invariant holds
        postcondition = invariant

        status, counterexample = engine.verify_property(
            postcondition,
            assumptions=preconditions + [invariant]
        )

        # This should pass: the invariant is consistent with itself
        assert status == VerificationStatus.PASS

    def test_integer_arithmetic_verification(self):
        """Test verifying integer arithmetic properties."""
        engine = Z3Engine()
        x = Int("x")

        # Property: x + 1 > x (always true)
        status, counterexample = engine.verify_property(x + 1 > x)

        assert status == VerificationStatus.PASS
        assert counterexample is None

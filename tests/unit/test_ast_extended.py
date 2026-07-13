"""Unit tests for extended AST converter (loops, array bounds)."""

import pytest

from verityai.symbolic.ast_to_smt import ASTtoSMTConverter, VerifiableSubsetViolation


class TestASTConverterLoops:
    """Tests for loop verification in AST converter."""

    def test_convert_range_loop_simple(self):
        """Test converting simple range loop with basic operations."""
        converter = ASTtoSMTConverter()
        code = """
total = 0
for i in range(10):
    pass
"""
        constraints, non_verifiable = converter.convert_code(code)

        # Should process without error, loop bounds should be captured
        assert len(constraints) >= 1

    def test_convert_range_loop_with_bounds(self):
        """Test converting range loop with start/end bounds."""
        converter = ASTtoSMTConverter()
        code = """
for i in range(5, 10):
    x = i + 1
"""
        constraints, non_verifiable = converter.convert_code(code)

        # Should extract bounds
        assert len(constraints) > 0

    def test_loop_with_non_simple_target_marked_non_verifiable(self):
        """Test that non-simple loop targets are marked non-verifiable."""
        converter = ASTtoSMTConverter(allow_partial=True)
        code = """
for data[i] in items:
    pass
"""
        constraints, non_verifiable = converter.convert_code(code)

        # Complex loop target should be marked non-verifiable
        assert len(non_verifiable) > 0

    def test_non_range_loop_marked_non_verifiable(self):
        """Test that non-range loops are marked non-verifiable."""
        converter = ASTtoSMTConverter(allow_partial=True)
        code = """
for x in items:
    y = x + 1
"""
        constraints, non_verifiable = converter.convert_code(code)

        # Non-range loop should be marked as non-verifiable
        assert len(non_verifiable) > 0


class TestASTConverterArrayBounds:
    """Tests for array bounds checking in AST converter."""

    def test_len_call_creates_symbolic_length(self):
        """Test that len(arr) creates symbolic length variable."""
        converter = ASTtoSMTConverter()
        code = "x = len(arr)"

        constraints, non_verifiable = converter.convert_code(code)

        # Should create symbolic variable and add constraint
        assert len(constraints) >= 1
        assert "len_arr" in converter.variables

    def test_len_on_complex_expression_fails(self):
        """Test that len() on complex expressions raises error."""
        converter = ASTtoSMTConverter(allow_partial=False)
        code = "x = len(arr[0])"

        with pytest.raises(VerifiableSubsetViolation):
            converter.convert_code(code)

    def test_len_with_multiple_calls(self):
        """Test that multiple len() calls reuse the same variable."""
        converter = ASTtoSMTConverter()
        code = """
n1 = len(arr)
n2 = len(arr)
"""
        constraints, non_verifiable = converter.convert_code(code)

        # Both should use same symbolic variable
        assert "len_arr" in converter.variables
        # Should have at least the array length constraint
        assert len(constraints) >= 1

    def test_len_creates_positivity_constraint(self):
        """Test that len() creates len > 0 constraint."""
        converter = ASTtoSMTConverter()
        code = "n = len(arr)"

        constraints, non_verifiable = converter.convert_code(code)

        # Should have constraint that len_arr > 0
        assert any("len_arr" in str(c) for c in constraints)


class TestASTConverterSSASoundness:
    """Regression tests for SSA variable versioning (Week 4 correctness fix).

    Without versioning, `x = x + 1` translates to the Z3 constraint
    `x == x + 1`, which is unsatisfiable for every integer — the converter
    would report correct, ordinary code as failing verification. These
    tests pin down that reassignment is handled soundly.
    """

    def test_sequential_reassignment_is_satisfiable(self):
        """x = 5; x = x + 1; assert x == 6 must be SAT, not UNSAT."""
        from verityai.symbolic.z3_engine import Z3Engine

        converter = ASTtoSMTConverter()
        code = "x = 5\nx = x + 1\nassert x == 6"
        constraints, non_verifiable = converter.convert_code(code)

        assert non_verifiable == []

        engine = Z3Engine()
        status, _ = engine.check_satisfiable(constraints)
        assert status.value == "pass"  # SAT

    def test_aug_assign_sequential_is_satisfiable(self):
        """x = 5; x += 1; assert x == 6 must be SAT (AugAssign SSA)."""
        from verityai.symbolic.z3_engine import Z3Engine

        converter = ASTtoSMTConverter()
        code = "x = 5\nx += 1\nassert x == 6"
        constraints, non_verifiable = converter.convert_code(code)

        assert non_verifiable == []

        engine = Z3Engine()
        status, _ = engine.check_satisfiable(constraints)
        assert status.value == "pass"

    def test_reassignment_to_wrong_value_is_unsat(self):
        """x = 5; x = x + 1; assert x == 100 must be UNSAT (still catches real bugs)."""
        from verityai.symbolic.z3_engine import Z3Engine

        converter = ASTtoSMTConverter()
        code = "x = 5\nx = x + 1\nassert x == 100"
        constraints, non_verifiable = converter.convert_code(code)

        engine = Z3Engine()
        status, _ = engine.check_satisfiable(constraints)
        assert status.value == "fail"  # UNSAT

    def test_if_branch_does_not_leak_unconditional_constraint(self):
        """Assignment inside an if-body must not become an unconditional
        constraint outside that branch (the pre-Week-4 double-processing bug).
        """
        from verityai.symbolic.z3_engine import Z3Engine

        converter = ASTtoSMTConverter()
        code = """
x = 10
if x > 100:
    y = 1
else:
    y = 2
assert y == 2
"""
        constraints, non_verifiable = converter.convert_code(code)

        engine = Z3Engine()
        status, _ = engine.check_satisfiable(constraints)
        # x=10 takes the else branch, so y must be 2 -> satisfiable
        assert status.value == "pass"

    def test_aug_assign_in_loop_body_does_not_crash(self):
        """Real seed-data pattern: accumulator += x inside a range loop."""
        converter = ASTtoSMTConverter()
        code = """
total = 0
for i in range(5):
    total += i
"""
        constraints, non_verifiable = converter.convert_code(code)
        # Should not crash; loop accumulator pattern is at least partially handled
        assert isinstance(constraints, list)


class TestASTConverterIntegration:
    """Integration tests for extended converter."""

    def test_loop_with_assignment(self):
        """Test loop with simple assignment."""
        converter = ASTtoSMTConverter()
        code = """
total = 0
for i in range(10):
    total = total + i
"""
        constraints, non_verifiable = converter.convert_code(code)

        # Should handle loop and assignments
        assert len(constraints) > 0
        # Loop should be processed (not marked non-verifiable)
        loop_non_verifiable = [n for n in non_verifiable if "range" in str(n).lower()]
        assert len(loop_non_verifiable) == 0

    def test_loop_bounds_constraint(self):
        """Test that loop bounds are represented in constraints."""
        converter = ASTtoSMTConverter()
        code = """
for idx in range(10):
    val = idx
"""
        constraints, non_verifiable = converter.convert_code(code)

        # Should create constraints for loop bounds
        assert len(constraints) >= 1  # Loop bounds constraint

    def test_simple_verification_pattern(self):
        """Test verifiable pattern: array length check."""
        converter = ASTtoSMTConverter()
        code = """
arr_len = len(arr)
x = 5
"""
        constraints, non_verifiable = converter.convert_code(code)

        # Should have constraints for len() call (len_arr > 0)
        assert len(constraints) >= 1
        assert len(non_verifiable) == 0
        # Should have variables for both arr_len and x
        assert "len_arr" in converter.variables
        assert "arr_len" in converter.variables

    def test_mixed_verifiable_and_non_verifiable(self):
        """Test code with both verifiable and non-verifiable parts."""
        converter = ASTtoSMTConverter(allow_partial=True)
        code = """
for i in range(10):
    x = i * 2
result = external_function()
"""
        constraints, non_verifiable = converter.convert_code(code)

        # Should have some constraints but also mark non-verifiable parts
        assert len(non_verifiable) > 0  # Function call
        # Loop should still be processed
        assert len(constraints) > 0

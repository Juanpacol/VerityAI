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

"""Unit tests for ADR-0002: verifying asserts over function parameters,
and the if/else phi-merge fix folded into the same change.
"""

from verityai.ontology.models import VerificationStatus
from verityai.symbolic.ast_to_smt import ASTtoSMTConverter
from verityai.symbolic.verify import verify_python_snippet


class TestParameterBinding:
    def test_parameter_is_registered_and_typed_int(self):
        converter = ASTtoSMTConverter()
        converter.convert_code("def f(x):\n    assert x == x\n")

        assert converter.parameters == ["x"]
        assert "x" in converter.variables

    def test_no_parameters_leaves_parameters_list_empty(self):
        converter = ASTtoSMTConverter()
        converter.convert_code("x = 1\nassert x == 1\n")

        assert converter.parameters == []


class TestAssertOverUnconstrainedParameter:
    def test_tautology_independent_of_parameter_passes(self):
        result = verify_python_snippet("def f(x):\n    assert x == x\n")
        assert result.status == VerificationStatus.PASS

    def test_self_contradiction_fails_regardless_of_parameter(self):
        result = verify_python_snippet("def f(x):\n    assert x > 0 and x < 0\n")
        assert result.status == VerificationStatus.FAIL

    def test_guard_with_no_precondition_fails(self):
        """Without a stated PRE, an assert on a free parameter must hold for
        every integer -- most guard clauses don't, by design (that's why
        they're guards), so this must FAIL, not silently pass."""
        code = "def safe_divide(numerator, denominator):\n    assert denominator != 0\n    result = numerator // denominator\n    return result\n"
        result = verify_python_snippet(code)
        assert result.status == VerificationStatus.FAIL
        assert result.violations
        assert result.violations[0].input_values["denominator"] == 0


class TestPreconditionWiring:
    def test_guard_matching_stated_precondition_passes(self):
        code = (
            "def safe_divide(numerator, denominator):\n"
            '    """PRE: denominator != 0"""\n'
            "    assert denominator != 0\n"
            "    result = numerator // denominator\n"
            "    return result\n"
        )
        result = verify_python_snippet(code)
        assert result.status == VerificationStatus.PASS

    def test_guard_stricter_than_stated_precondition_fails(self):
        """PRE says > 0 but the assert demands > 100 -- the guard doesn't
        match its own stated contract, and that mismatch must be caught."""
        code = (
            "def safe_divide(numerator, denominator):\n"
            '    """PRE: denominator > 0"""\n'
            "    assert denominator > 100\n"
            "    result = numerator // denominator\n"
            "    return result\n"
        )
        result = verify_python_snippet(code)
        assert result.status == VerificationStatus.FAIL

    def test_unparseable_pre_is_skipped_not_crashed(self):
        code = 'def f(x):\n    """PRE: not valid python ((("""\n    assert x == x\n'
        result = verify_python_snippet(code)
        # Falls back to no PRE assumption; the tautology still passes on its own.
        assert result.status == VerificationStatus.PASS

    def test_docstring_alone_is_not_marked_non_verifiable(self):
        converter = ASTtoSMTConverter()
        _, non_verifiable = converter.convert_code(
            'def f(x):\n    """PRE: x != 0"""\n    assert x != 0\n'
        )
        assert non_verifiable == []


class TestIfElseWithParameters:
    def test_merged_branches_with_matching_precondition_passes(self):
        code = (
            "def clamp_positive(x):\n"
            '    """PRE: x != 0"""\n'
            "    if x > 0:\n"
            "        result = x\n"
            "    else:\n"
            "        result = -x\n"
            "    assert result > 0\n"
            "    return result\n"
        )
        result = verify_python_snippet(code)
        assert result.status == VerificationStatus.PASS

    def test_missing_precondition_surfaces_the_edge_case(self):
        """Without excluding x == 0, both branches produce result == 0 there,
        breaking `assert result > 0` -- a real, findable counterexample."""
        code = (
            "def clamp_positive(x):\n"
            "    if x > 0:\n"
            "        result = x\n"
            "    else:\n"
            "        result = -x\n"
            "    assert result > 0\n"
            "    return result\n"
        )
        result = verify_python_snippet(code)
        assert result.status == VerificationStatus.FAIL
        assert result.violations[0].input_values["x"] == 0


class TestLoopBoundAsAssumption:
    def test_assert_within_declared_loop_bounds_passes(self):
        code = "def sum_up_to(n):\n    total = 0\n    for i in range(n):\n        assert i < n\n"
        result = verify_python_snippet(code)
        assert result.status == VerificationStatus.PASS


class TestPhiMergeRegressionBothBranchOrders:
    """The pre-existing if/else bug only showed up when the TRUE branch was
    the one processed FIRST (`if`), since the `else` branch (processed
    last) always used to win as the 'current' binding regardless of which
    branch actually ran. Check both orders explicitly.
    """

    def test_else_branch_true_case(self):
        code = (
            "def compute_max():\n"
            "    a = 3\n"
            "    b = 7\n"
            "    if a > b:\n"
            "        result = a\n"
            "    else:\n"
            "        result = b\n"
            "    assert result == 7\n"
            "    return result\n"
        )
        assert verify_python_snippet(code).status == VerificationStatus.PASS

    def test_if_branch_true_case(self):
        """Same shape, but the `if` branch (processed first) is the one
        that's actually true -- this is exactly the case the old bug got
        wrong, since the orphaned `if`-branch value was never linked to
        anything the final assert could see."""
        code = (
            "def compute_max():\n"
            "    a = 7\n"
            "    b = 3\n"
            "    if a > b:\n"
            "        result = a\n"
            "    else:\n"
            "        result = b\n"
            "    assert result == 7\n"
            "    return result\n"
        )
        assert verify_python_snippet(code).status == VerificationStatus.PASS

    def test_if_branch_true_case_catches_real_bug(self):
        code = (
            "def compute_max():\n"
            "    a = 7\n"
            "    b = 3\n"
            "    if a > b:\n"
            "        result = a\n"
            "    else:\n"
            "        result = b\n"
            "    assert result == 3\n"
            "    return result\n"
        )
        assert verify_python_snippet(code).status == VerificationStatus.FAIL

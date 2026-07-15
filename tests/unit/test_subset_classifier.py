"""Unit tests for evidence/subset_classifier.py -- T3's ground-truth
classifier for "is this code inside VerityAI's verifiable subset".

Ground truth is the real ASTtoSMTConverter (symbolic/ast_to_smt.py), not a
reimplementation -- these tests exercise real code samples through it, the
same object the actual verifier uses.
"""

from verityai.evidence.subset_classifier import classify_problem


class TestFullyVerifiableCode:
    def test_simple_int_bool_if_else_is_a_subset_member(self):
        code = "def f(x: int, y: int) -> int:\n    if x > y:\n        return x\n    return y\n"
        result = classify_problem(code)
        assert result.subset_member is True
        assert result.non_verifiable_nodes == []
        assert result.exclusion_categories == []

    def test_bounded_for_range_loop_is_a_subset_member(self):
        code = "def f(n: int) -> int:\n    total = 0\n    for i in range(n):\n        total = total + i\n    return total\n"
        result = classify_problem(code)
        assert result.subset_member is True

    def test_basic_string_equality_and_concatenation_is_a_subset_member(self):
        # T3's headline finding (6.1%/8.8% coverage) was measured before
        # basic Z3 String theory support existed in ast_to_smt.py -- this
        # documents the subset actually grew, at the classifier level.
        code = "def f(a: str, b: str) -> bool:\n    return a + b == 'ab'\n"
        result = classify_problem(code)
        assert result.subset_member is True
        assert result.exclusion_categories == []

    def test_len_on_typed_string_parameter_is_a_subset_member(self):
        code = "def f(s: str) -> int:\n    return len(s)\n"
        result = classify_problem(code)
        assert result.subset_member is True


class TestSyntaxError:
    def test_syntax_error_is_excluded_with_its_own_category(self):
        result = classify_problem("def f(:\n  pass")
        assert result.subset_member is False
        assert result.exclusion_categories == ["syntax_error"]


class TestExclusionBuckets:
    def test_string_method_call_still_categorized_as_unsupported_call(self):
        # Basic string equality/concatenation entered the verifiable subset
        # (see TestBasicStringSupport below) -- method calls did not, since
        # _convert_expr has no method-call dispatch for any type.
        code = "def f(s: str) -> str:\n    return s.upper()\n"
        result = classify_problem(code)
        assert result.subset_member is False
        assert "unsupported_call" in result.exclusion_categories

    def test_fstring_still_categorized_as_unsupported_expression(self):
        code = 'def f(s: str) -> str:\n    return f"{s}!"\n'
        result = classify_problem(code)
        assert result.subset_member is False
        assert "unsupported_expression" in result.exclusion_categories

    def test_list_iteration_categorized_as_container_ops(self):
        code = "def f(xs):\n    total = 0\n    for x in xs:\n        total = total + x\n    return total\n"
        result = classify_problem(code)
        assert result.subset_member is False
        assert "container_ops" in result.exclusion_categories

    def test_while_loop_categorized_as_while_loop(self):
        code = (
            "def f(n: int) -> int:\n    i = 0\n    while i < n:\n        i = i + 1\n    return i\n"
        )
        result = classify_problem(code)
        assert result.subset_member is False
        assert "while_loop" in result.exclusion_categories

    def test_self_recursion_categorized_as_recursion(self):
        code = "def fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)\n"
        result = classify_problem(code)
        assert result.subset_member is False
        assert "recursion" in result.exclusion_categories

    def test_try_except_categorized_as_exceptions(self):
        code = "def f(x: int) -> int:\n    try:\n        return x\n    except Exception:\n        return 0\n"
        result = classify_problem(code)
        assert result.subset_member is False
        assert "exceptions" in result.exclusion_categories

    def test_import_categorized_as_import_statement_not_dumped_into_other(self):
        code = "from typing import List\n\n\ndef f(x: int) -> int:\n    return x\n"
        result = classify_problem(code)
        assert result.subset_member is False
        assert "import_statement" in result.exclusion_categories

    def test_method_call_categorized_as_unsupported_call(self):
        code = "def f(x: str) -> str:\n    return x.strip()\n"
        result = classify_problem(code)
        assert result.subset_member is False
        assert "unsupported_call" in result.exclusion_categories


class TestBucketingNeverOverridesGroundTruth:
    def test_subset_member_false_whenever_any_non_verifiable_node_exists(self):
        code = "def f(xs):\n    return xs[0]\n"
        result = classify_problem(code)
        assert result.subset_member == (len(result.non_verifiable_nodes) == 0)

    def test_clean_code_has_no_exclusion_categories_even_with_docstring(self):
        code = 'def f(x: int) -> int:\n    """A docstring that should NOT count as string_ops."""\n    return x\n'
        result = classify_problem(code)
        assert result.subset_member is True
        assert result.exclusion_categories == []


class TestConverterErrorDegradation:
    def test_unexpected_converter_exception_is_reported_not_raised(self):
        # A function whose only content is a bare recursive return -- this
        # exercises the Return-inspection path (Real run #1's regression
        # fix) rather than crashing; documents that classify_problem
        # degrades instead of propagating even for edge-case inputs.
        code = "def f(n):\n    return n * f(n - 1)\n"
        result = classify_problem(code)
        assert result.subset_member is False

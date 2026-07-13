"""Unit tests for incremental verification (agent/refinement.py)."""

from verityai.agent.refinement import IncrementalVerifier, extract_functions
from verityai.ontology.models import VerificationResult, VerificationStatus

TWO_FUNCTIONS = """
def foo(x):
    return x + 1

def bar(y):
    return y - 1
"""


class TestExtractFunctions:
    def test_extracts_each_top_level_function(self):
        functions = extract_functions(TWO_FUNCTIONS)

        assert set(functions.keys()) == {"foo", "bar"}
        assert "def foo(x):" in functions["foo"]
        assert "def bar(y):" in functions["bar"]

    def test_returns_empty_dict_for_code_with_no_functions(self):
        assert extract_functions("x = 1\nassert x == 1") == {}


class FakeVerifier:
    """Records every code string it was asked to verify, in order."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, code: str) -> VerificationResult:
        self.calls.append(code)
        return VerificationResult(code_id="", status=VerificationStatus.PASS, confidence=1.0)


class TestIncrementalVerifierCaching:
    def test_first_verify_checks_every_function(self):
        verifier = FakeVerifier()
        incremental = IncrementalVerifier(verifier)

        incremental.verify(TWO_FUNCTIONS)

        assert len(verifier.calls) == 2
        assert set(incremental.last_reverified) == {"foo", "bar"}

    def test_second_verify_with_no_changes_reverifies_nothing(self):
        verifier = FakeVerifier()
        incremental = IncrementalVerifier(verifier)

        incremental.verify(TWO_FUNCTIONS)
        verifier.calls.clear()
        incremental.verify(TWO_FUNCTIONS)

        assert verifier.calls == []
        assert incremental.last_reverified == []

    def test_changing_one_function_only_reverifies_that_function(self):
        verifier = FakeVerifier()
        incremental = IncrementalVerifier(verifier)
        incremental.verify(TWO_FUNCTIONS)
        verifier.calls.clear()

        changed = TWO_FUNCTIONS.replace("return x + 1", "return x + 2")
        incremental.verify(changed)

        assert incremental.last_reverified == ["foo"]
        assert len(verifier.calls) == 1
        assert "x + 2" in verifier.calls[0]

    def test_no_top_level_functions_falls_back_to_full_verify_uncached(self):
        verifier = FakeVerifier()
        incremental = IncrementalVerifier(verifier)

        incremental.verify("x = 1\nassert x == 1")
        incremental.verify("x = 1\nassert x == 1")

        assert len(verifier.calls) == 2  # no caching without a function to key on
        assert incremental.last_reverified == ["<module>"]

    def test_removed_function_is_dropped_from_cache(self):
        verifier = FakeVerifier()
        incremental = IncrementalVerifier(verifier)
        incremental.verify(TWO_FUNCTIONS)

        only_foo = "def foo(x):\n    return x + 1\n"
        incremental.verify(only_foo)

        assert "bar" not in incremental._cache


class TestIncrementalVerifierCombining:
    def test_one_failing_function_makes_overall_result_fail(self):
        def verify_fn(code: str) -> VerificationResult:
            status = VerificationStatus.FAIL if "bar" in code else VerificationStatus.PASS
            return VerificationResult(code_id="", status=status, confidence=0.5 if "bar" in code else 1.0)

        incremental = IncrementalVerifier(verify_fn)
        result = incremental.verify(TWO_FUNCTIONS)

        assert result.status == VerificationStatus.FAIL
        assert result.confidence == 0.5
        assert result.metadata["per_function"]["bar"] == "fail"

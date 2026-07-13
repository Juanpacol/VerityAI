"""Unit tests for weighted confidence scoring."""

import pytest

from verityai.agent.confidence import compute_confidence
from verityai.ontology.models import Counterexample, VerificationResult, VerificationStatus


def make_result(status: VerificationStatus, confidence: float = 1.0, with_violation: bool = False) -> VerificationResult:
    violations = []
    if with_violation:
        violations = [
            Counterexample(rule_id="test_rule", input_values={"x": 1}, description="test violation")
        ]
    return VerificationResult(
        code_id="test",
        status=status,
        confidence=confidence,
        violations=violations,
    )


class TestConfidenceWeights:
    """Tests validating the weighting formula itself."""

    def test_weights_sum_to_one(self):
        from verityai.agent.confidence import (
            WEIGHT_COMPLEXITY,
            WEIGHT_PATTERN_SIMILARITY,
            WEIGHT_TEST_COVERAGE,
            WEIGHT_VERIFICATION,
        )

        total = WEIGHT_VERIFICATION + WEIGHT_PATTERN_SIMILARITY + WEIGHT_COMPLEXITY + WEIGHT_TEST_COVERAGE
        assert abs(total - 1.0) < 1e-9

    def test_verification_is_dominant_weight(self):
        from verityai.agent.confidence import WEIGHT_VERIFICATION

        assert WEIGHT_VERIFICATION == 0.50


class TestComputeConfidencePass:
    """Tests for PASS verification results."""

    def test_perfect_pass_with_all_signals_high(self):
        result = make_result(VerificationStatus.PASS, confidence=1.0)
        confidence = compute_confidence(
            result, pattern_similarity=1.0, complexity_score=1.0, test_coverage=1.0
        )
        assert confidence == pytest.approx(1.0)

    def test_pass_alone_gives_half_confidence(self):
        """With zero pattern/complexity/coverage signal, PASS alone should cap near 0.5 + baseline."""
        result = make_result(VerificationStatus.PASS, confidence=1.0)
        confidence = compute_confidence(result, pattern_similarity=0.0, complexity_score=0.0, test_coverage=0.0)
        # Only WEIGHT_VERIFICATION (0.5) contributes
        assert confidence == pytest.approx(0.5)

    def test_pass_uses_verifier_own_confidence(self):
        """A PASS with low internal Z3 confidence (many UNKNOWNs) should score lower."""
        high_conf_result = make_result(VerificationStatus.PASS, confidence=1.0)
        low_conf_result = make_result(VerificationStatus.PASS, confidence=0.3)

        high = compute_confidence(high_conf_result)
        low = compute_confidence(low_conf_result)

        assert high > low


class TestComputeConfidenceFail:
    """Tests for FAIL verification results."""

    def test_fail_gives_zero_verification_component(self):
        result = make_result(VerificationStatus.FAIL, confidence=1.0, with_violation=True)
        confidence = compute_confidence(
            result, pattern_similarity=1.0, complexity_score=1.0, test_coverage=1.0
        )
        # Only the non-verification weights (0.25 + 0.15 + 0.10 = 0.5) can contribute
        assert confidence == pytest.approx(0.5)

    def test_fail_never_exceeds_fifty_percent(self):
        """A FAIL must never look more trustworthy than a PASS, regardless of other signals."""
        fail_result = make_result(VerificationStatus.FAIL, with_violation=True)
        pass_result = make_result(VerificationStatus.PASS, confidence=0.1)

        fail_confidence = compute_confidence(fail_result, pattern_similarity=1.0, complexity_score=1.0, test_coverage=1.0)
        pass_confidence = compute_confidence(pass_result, pattern_similarity=0.0, complexity_score=0.0, test_coverage=0.0)

        assert fail_confidence <= 0.5


class TestComputeConfidenceUnknownTimeout:
    """Tests for UNKNOWN/TIMEOUT verification results."""

    def test_unknown_discounted_relative_to_pass(self):
        pass_result = make_result(VerificationStatus.PASS, confidence=0.9)
        unknown_result = make_result(VerificationStatus.UNKNOWN, confidence=0.9)

        pass_confidence = compute_confidence(pass_result)
        unknown_confidence = compute_confidence(unknown_result)

        assert unknown_confidence < pass_confidence

    def test_timeout_treated_same_as_unknown(self):
        unknown_result = make_result(VerificationStatus.UNKNOWN, confidence=0.7)
        timeout_result = make_result(VerificationStatus.TIMEOUT, confidence=0.7)

        assert compute_confidence(unknown_result) == compute_confidence(timeout_result)


class TestComputeConfidenceNotVerified:
    """Tests for NOT_VERIFIED (ADR-0001 degraded mode) results."""

    def test_not_verified_gets_baseline_not_zero(self):
        """Code outside the verifiable subset wasn't proven wrong, just unprovable."""
        result = make_result(VerificationStatus.NOT_VERIFIED)
        confidence = compute_confidence(result)
        assert confidence > 0.0

    def test_not_verified_scores_below_pass(self):
        pass_result = make_result(VerificationStatus.PASS, confidence=1.0)
        not_verified_result = make_result(VerificationStatus.NOT_VERIFIED)

        assert compute_confidence(not_verified_result) < compute_confidence(pass_result)


class TestComputeConfidenceValidation:
    """Tests for input validation."""

    def test_rejects_pattern_similarity_out_of_range(self):
        result = make_result(VerificationStatus.PASS)
        with pytest.raises(ValueError):
            compute_confidence(result, pattern_similarity=1.5)

    def test_rejects_negative_complexity_score(self):
        result = make_result(VerificationStatus.PASS)
        with pytest.raises(ValueError):
            compute_confidence(result, complexity_score=-0.1)

    def test_rejects_test_coverage_out_of_range(self):
        result = make_result(VerificationStatus.PASS)
        with pytest.raises(ValueError):
            compute_confidence(result, test_coverage=2.0)

    def test_result_always_clamped_to_unit_interval(self):
        result = make_result(VerificationStatus.PASS, confidence=1.0)
        confidence = compute_confidence(
            result, pattern_similarity=1.0, complexity_score=1.0, test_coverage=1.0
        )
        assert 0.0 <= confidence <= 1.0

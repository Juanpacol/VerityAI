"""Weighted confidence scoring for generated code.

Formula (per Phase 2 plan): verification 50%, pattern similarity 25%,
complexity appropriateness 15%, test coverage 10%.
"""

from verityai.ontology.models import VerificationResult, VerificationStatus

WEIGHT_VERIFICATION = 0.50
WEIGHT_PATTERN_SIMILARITY = 0.25
WEIGHT_COMPLEXITY = 0.15
WEIGHT_TEST_COVERAGE = 0.10

assert (
    abs(
        (WEIGHT_VERIFICATION + WEIGHT_PATTERN_SIMILARITY + WEIGHT_COMPLEXITY + WEIGHT_TEST_COVERAGE)
        - 1.0
    )
    < 1e-9
), "Confidence weights must sum to 1.0"


def compute_confidence(
    verification_result: VerificationResult,
    pattern_similarity: float = 0.0,
    complexity_score: float = 0.5,
    test_coverage: float = 0.0,
) -> float:
    """Compute the weighted confidence score for one generation attempt.

    Args:
        verification_result: Result from the symbolic verification layer
        pattern_similarity: 0.0-1.0, similarity to a known-verified KG pattern
            (0.0 when no KG pattern match was attempted/found)
        complexity_score: 0.0-1.0, how well the code's actual complexity
            matches the expected complexity class (defaults to a neutral 0.5
            when no complexity analysis was performed)
        test_coverage: 0.0-1.0, fraction of available test cases passed
            (0.0 when no test cases were run)

    Returns:
        Weighted confidence score, clamped to [0.0, 1.0]

    Raises:
        ValueError: If any component score is outside [0.0, 1.0]
    """
    for name, value in (
        ("pattern_similarity", pattern_similarity),
        ("complexity_score", complexity_score),
        ("test_coverage", test_coverage),
    ):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be in [0.0, 1.0], got {value}")

    verification_component = _verification_component(verification_result)

    confidence = (
        WEIGHT_VERIFICATION * verification_component
        + WEIGHT_PATTERN_SIMILARITY * pattern_similarity
        + WEIGHT_COMPLEXITY * complexity_score
        + WEIGHT_TEST_COVERAGE * test_coverage
    )

    return max(0.0, min(1.0, confidence))


def explain_confidence(
    verification_result: VerificationResult,
    pattern_similarity: float = 0.0,
    complexity_score: float = 0.5,
    test_coverage: float = 0.0,
) -> dict:
    """Same computation as compute_confidence, but returns the full breakdown.

    compute_confidence collapses all four weighted factors into one float,
    discarding exactly the information the reasoning-trace view (Commit 6)
    needs to explain *why* a confidence score is what it is. This does not
    replace compute_confidence (existing tests/callers keep using it
    unchanged) — `total` here is guaranteed identical to what
    compute_confidence returns for the same inputs.

    Args:
        verification_result: Result from the symbolic verification layer
        pattern_similarity: 0.0-1.0, similarity to a known-verified KG pattern
        complexity_score: 0.0-1.0, how well complexity matches expectations
        test_coverage: 0.0-1.0, fraction of available test cases passed

    Returns:
        {"total": float, "components": {...}, "weights": {...}}

    Raises:
        ValueError: If any component score is outside [0.0, 1.0]
    """
    for name, value in (
        ("pattern_similarity", pattern_similarity),
        ("complexity_score", complexity_score),
        ("test_coverage", test_coverage),
    ):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be in [0.0, 1.0], got {value}")

    components = {
        "verification": _verification_component(verification_result),
        "pattern_similarity": pattern_similarity,
        "complexity": complexity_score,
        "test_coverage": test_coverage,
    }
    weights = {
        "verification": WEIGHT_VERIFICATION,
        "pattern_similarity": WEIGHT_PATTERN_SIMILARITY,
        "complexity": WEIGHT_COMPLEXITY,
        "test_coverage": WEIGHT_TEST_COVERAGE,
    }
    total = max(0.0, min(1.0, sum(components[k] * weights[k] for k in components)))

    return {"total": total, "components": components, "weights": weights}


def _verification_component(result: VerificationResult) -> float:
    """Map a VerificationResult to its [0.0, 1.0] contribution to confidence.

    PASS uses the verifier's own confidence directly (Z3Engine already
    discounts this for its unknown-query rate). FAIL is always 0 — a
    counterexample was found, no amount of pattern similarity should paper
    over that. UNKNOWN/TIMEOUT further discount the verifier's own
    confidence since the solver couldn't reach a definitive answer.
    NOT_VERIFIED (code outside the verifiable subset, ADR-0001) gets a small
    fixed baseline rather than 0, since such code was not proven wrong —
    only unprovable with this system's current scope.
    """
    if result.status == VerificationStatus.PASS:
        return result.confidence
    elif result.status == VerificationStatus.FAIL:
        return 0.0
    elif result.status in (VerificationStatus.UNKNOWN, VerificationStatus.TIMEOUT):
        return result.confidence * 0.5
    elif result.status == VerificationStatus.NOT_VERIFIED:
        return 0.3
    return 0.0

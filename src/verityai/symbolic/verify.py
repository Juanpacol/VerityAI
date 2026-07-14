"""Shared AST -> Z3 verification, for both concrete code and parameterized functions.

Extracted from Orchestrator.verify_code so the same check can be reused
for a purpose beyond verifying LLM-generated code: validating a candidate
KG rule's `test_code` before it's allowed anywhere near human approval
(see agent/rule_validation.py).
"""

from verityai.ontology.models import Counterexample, VerificationResult, VerificationStatus
from verityai.symbolic.ast_to_smt import ASTtoSMTConverter
from verityai.symbolic.security_scan import scan_for_dangerous_patterns
from verityai.symbolic.z3_engine import Z3Engine

# Worst-status-wins precedence, used when combining results across multiple
# assert_properties in the parameterized path (see _verify_parameterized).
_STATUS_PRECEDENCE = {
    VerificationStatus.FAIL: 0,
    VerificationStatus.TIMEOUT: 1,
    VerificationStatus.UNKNOWN: 2,
    VerificationStatus.NOT_VERIFIED: 3,
    VerificationStatus.PASS: 4,
}


def verify_python_snippet(code: str, timeout_seconds: float = 3.0) -> VerificationResult:
    """Convert a Python snippet to Z3 constraints and verify it.

    MVP verification scope: without a target postcondition, "verification"
    means the snippet's own extracted constraints (including any assert
    statements) are internally satisfiable, plus reporting what fraction
    fell outside ADR-0001's verifiable subset. Real, if limited — catches
    self-contradictory logic and flags unverifiable code honestly rather
    than silently passing it.

    ADR-0002: when the snippet defines a function with parameters, this
    routes to _verify_parameterized instead — an assert referencing a free
    parameter must be checked for validity (holds for every value), not
    satisfiability (some value exists), or every such assert would trivially
    "pass" regardless of whether the function is actually correct.

    Security (Phase 4 Part D): before any of the above, the snippet is
    scanned for dangerous constructs (os.system, eval/exec, subprocess,
    pickle.loads, ...) via security_scan.py. A match forces FAIL
    unconditionally — Z3 has no opinion on whether code is dangerous, only
    on whether its own assertions are consistent, so this check can't be
    skipped just because the code "verifies."
    """
    # Syntax-error findings are excluded here and left to the existing
    # SyntaxError handling below (different metadata shape, and
    # AgentState._summarize_failure specifically reads metadata["error"])
    # -- a snippet that fails to parse isn't a "dangerous pattern," it's
    # just malformed, and scan_for_dangerous_patterns only ever emits a
    # syntax_error finding when the code doesn't parse at all.
    security_findings = [
        f for f in scan_for_dangerous_patterns(code) if f.construct != "syntax_error"
    ]
    if security_findings:
        return VerificationResult(
            code_id="",
            status=VerificationStatus.FAIL,
            confidence=0.0,
            violations=[],
            z3_result=None,
            metadata={
                "blocked_reason": "dangerous_code_pattern",
                "security_findings": [
                    {"line": f.line, "construct": f.construct, "description": f.description}
                    for f in security_findings
                ],
            },
        )

    converter = ASTtoSMTConverter(allow_partial=True)

    try:
        constraints, non_verifiable = converter.convert_code(code)
    except SyntaxError as e:
        return VerificationResult(
            code_id="",
            status=VerificationStatus.FAIL,
            confidence=0.0,
            violations=[],
            z3_result=None,
            metadata={"error": f"Syntax error: {e}"},
        )

    if converter.parameters:
        return _verify_parameterized(converter, non_verifiable, timeout_seconds)

    if not constraints:
        status = VerificationStatus.NOT_VERIFIED if non_verifiable else VerificationStatus.PASS
        return VerificationResult(
            code_id="",
            status=status,
            confidence=0.3 if non_verifiable else 0.5,
            violations=[],
            z3_result=None,
            metadata={"non_verifiable_nodes": non_verifiable},
        )

    engine = Z3Engine(timeout_seconds=timeout_seconds)
    sat_status, _ = engine.check_satisfiable(constraints)

    if sat_status == VerificationStatus.FAIL:
        result_status = VerificationStatus.FAIL
        confidence = 0.0
    elif non_verifiable:
        result_status = VerificationStatus.NOT_VERIFIED
        confidence = engine.success_rate * 0.6
    else:
        result_status = sat_status  # PASS, UNKNOWN, or TIMEOUT
        confidence = engine.success_rate

    return VerificationResult(
        code_id="",
        status=result_status,
        confidence=confidence,
        violations=[],
        z3_result=sat_status.value,
        metadata={
            "non_verifiable_nodes": non_verifiable,
            "total_queries": engine.total_queries,
        },
    )


def _verify_parameterized(
    converter: ASTtoSMTConverter, non_verifiable: list[dict], timeout_seconds: float
) -> VerificationResult:
    """Verify a function with free parameters (ADR-0002).

    Each recorded assert is proven against path_constraints (assignments,
    branch/loop conditions — never other asserts) plus its own branch
    assumptions, via Z3Engine.verify_property. The overall status is the
    worst result across all asserts; with none at all, there's nothing to
    prove, so this falls back to the same PASS/NOT_VERIFIED ambiguity the
    non-parameterized path uses when it has no constraints either.
    """
    if not converter.assert_properties:
        status = VerificationStatus.NOT_VERIFIED if non_verifiable else VerificationStatus.PASS
        return VerificationResult(
            code_id="",
            status=status,
            confidence=0.3 if non_verifiable else 0.5,
            violations=[],
            z3_result=None,
            metadata={"non_verifiable_nodes": non_verifiable, "parameters": converter.parameters},
        )

    engine = Z3Engine(timeout_seconds=timeout_seconds)
    worst_status = VerificationStatus.PASS
    counterexample_dict = None

    for property_expr, branch_assumptions in converter.assert_properties:
        assumptions = list(converter.path_constraints) + list(branch_assumptions)
        status, counterexample = engine.verify_property(property_expr, assumptions=assumptions)
        if _STATUS_PRECEDENCE[status] < _STATUS_PRECEDENCE[worst_status]:
            worst_status = status
            counterexample_dict = counterexample

    if non_verifiable and worst_status == VerificationStatus.PASS:
        result_status = VerificationStatus.NOT_VERIFIED
        confidence = engine.success_rate * 0.6
    elif worst_status == VerificationStatus.FAIL:
        result_status = VerificationStatus.FAIL
        confidence = 0.0
    else:
        result_status = worst_status
        confidence = engine.success_rate

    violations = []
    if counterexample_dict and worst_status == VerificationStatus.FAIL:
        violations.append(
            Counterexample(
                rule_id=None,
                input_values=counterexample_dict,
                description="Assert does not hold for these parameter values",
            )
        )

    return VerificationResult(
        code_id="",
        status=result_status,
        confidence=confidence,
        violations=violations,
        z3_result=worst_status.value,
        metadata={
            "non_verifiable_nodes": non_verifiable,
            "parameters": converter.parameters,
            "total_queries": engine.total_queries,
        },
    )

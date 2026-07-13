"""Unit tests for ontology models."""

import pytest
from verityai.ontology.models import (
    Algorithm,
    Counterexample,
    Pattern,
    Rule,
    VerificationResult,
    VerificationStatus,
)


def test_rule_creation():
    """Test creating a Rule."""
    rule = Rule(
        name="no_null_dereference",
        description="Ensure no null pointer dereferences",
        category="security",
        condition="If x is used, x must not be None",
        severity="critical",
        applies_to=["python", "java"],
    )

    assert rule.name == "no_null_dereference"
    assert rule.severity == "critical"
    assert "python" in rule.applies_to
    assert rule.id is not None


def test_algorithm_creation():
    """Test creating an Algorithm."""
    algorithm = Algorithm(
        name="binary_search",
        description="Search for target in sorted array",
        code="""def binary_search(arr, target):
    left, right = 0, len(arr) - 1
    while left <= right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1""",
        complexity_time="O(log n)",
        complexity_space="O(1)",
    )

    assert algorithm.name == "binary_search"
    assert algorithm.complexity_time == "O(log n)"
    assert "binary_search" in algorithm.code


def test_verification_result():
    """Test creating a VerificationResult."""
    result = VerificationResult(
        code_id="test_code_123",
        status=VerificationStatus.PASS,
        confidence=0.95,
    )

    assert result.status == VerificationStatus.PASS
    assert result.confidence == 0.95
    assert result.code_id == "test_code_123"


def test_counterexample():
    """Test creating a Counterexample."""
    from uuid import uuid4

    rule_id = uuid4()
    counterexample = Counterexample(
        rule_id=rule_id,
        input_values={"x": None},
        description="Null dereference on x",
        source_line=42,
        suggested_fix="Check if x is not None before use",
    )

    assert counterexample.rule_id == rule_id
    assert counterexample.input_values["x"] is None
    assert counterexample.source_line == 42


def test_pattern_creation():
    """Test creating a Pattern."""
    pattern = Pattern(
        name="safe_json_parsing",
        description="Safe JSON parsing with error handling",
        category="utility",
        code="""def parse_json_safe(s):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None""",
        language="python",
    )

    assert pattern.name == "safe_json_parsing"
    assert pattern.verified is True
    assert pattern.language == "python"

"""Unit tests for symbolic debugger."""

import pytest

from verityai.ontology.models import Counterexample, VerificationResult, VerificationStatus
from verityai.symbolic.debugger import SymbolicDebugger


class TestSymbolicDebugger:
    """Tests for SymbolicDebugger."""

    def test_debugger_creation(self):
        """Test debugger initialization."""
        source = "x = 5\nassert x > 0"
        debugger = SymbolicDebugger(source)

        assert debugger.source_code == source
        assert len(debugger.lines) == 2

    def test_find_suspicious_line_array_access(self):
        """Test finding line with array access."""
        source = """def search(arr, idx):
    return arr[idx]
"""
        debugger = SymbolicDebugger(source)
        ce = Counterexample(
            rule_id="bounds_check",
            input_values={"idx": 100},
            description="Array index out of bounds",
        )

        line = debugger._find_suspicious_line(ce)

        assert line is not None
        assert "arr[idx]" in debugger.lines[line - 1]

    def test_get_context(self):
        """Test getting source code context."""
        source = "x = 1\ny = 2\nz = 3\nresult = x + y + z\nreturn result"
        debugger = SymbolicDebugger(source)

        context = debugger._get_context(3, context_lines=1)

        assert "3" in context
        assert "→" in context
        assert "y = 2" in context

    def test_generate_fix_suggestions_bounds(self):
        """Test generating fix suggestions for bounds violations."""
        source = "return arr[idx]"
        debugger = SymbolicDebugger(source)

        ce = Counterexample(
            rule_id="bounds_check",
            input_values={"idx": -1, "arr_len": 10},
            description="Array index out of bounds",
        )

        suggestions = debugger._generate_fix_suggestions(ce, 1, "Array Bounds Check")

        assert len(suggestions) > 0
        assert any("assert" in s.lower() for s in suggestions)

    def test_generate_fix_suggestions_null_check(self):
        """Test generating fix suggestions for null dereference."""
        source = "return obj.method()"
        debugger = SymbolicDebugger(source)

        ce = Counterexample(
            rule_id="null_check",
            input_values={"obj": None},
            description="Null pointer dereference",
        )

        suggestions = debugger._generate_fix_suggestions(ce, 1, "No Null Dereference")

        assert any("null" in s.lower() or "none" in s.lower() for s in suggestions)

    def test_debug_violation(self):
        """Test debugging a single violation."""
        source = "x = 5\nassert x > 10"
        debugger = SymbolicDebugger(source)

        ce = Counterexample(
            rule_id="range_check",
            input_values={"x": 5},
            description="Value out of expected range",
        )

        debug_entry = debugger._debug_violation(ce, "Range Check Rule")

        assert "Check" in str(debug_entry.get("rule", ""))
        assert debug_entry["suspicious_line"] is not None
        assert "source_code" in debug_entry
        assert len(debug_entry["fix_suggestions"]) > 0

    def test_debug_full_result(self):
        """Test debugging full verification result."""
        source = "return arr[idx]"
        debugger = SymbolicDebugger(source)

        ce = Counterexample(
            rule_id="bounds",
            input_values={"idx": 100},
            description="Index out of bounds",
        )

        result = VerificationResult(
            code_id="test_code",
            status=VerificationStatus.FAIL,
            confidence=0.9,
            violations=[ce],
            z3_result="fail",
            z3_model=None,
            duration_seconds=0.1,
        )

        debug_info = debugger.debug(result, rule_name="Bounds Check")

        assert debug_info["status"] == "fail"
        assert debug_info["confidence"] == 0.9
        assert len(debug_info["violations"]) > 0

    def test_explain_failure_pass(self):
        """Test explaining a passing verification."""
        source = "x = 5"
        debugger = SymbolicDebugger(source)

        result = VerificationResult(
            code_id="test",
            status=VerificationStatus.PASS,
            confidence=0.95,
            violations=[],
            z3_result="sat",
            z3_model=None,
            duration_seconds=0.05,
        )

        explanation = debugger.explain_failure(result)

        assert "Verification passed" in explanation
        assert ("95" in explanation or "0.95" in explanation)

    def test_explain_failure_fail(self):
        """Test explaining a failed verification."""
        source = "x = 5\nassert x > 10"
        debugger = SymbolicDebugger(source)

        ce = Counterexample(
            rule_id="range",
            input_values={"x": 5},
            description="Range check failed",
        )

        result = VerificationResult(
            code_id="test",
            status=VerificationStatus.FAIL,
            confidence=0.85,
            violations=[ce],
            z3_result="unsat",
            z3_model=None,
            duration_seconds=0.1,
        )

        explanation = debugger.explain_failure(result)

        assert "FAILED" in explanation
        assert "Range check failed" in explanation

    def test_explain_failure_unknown(self):
        """Test explaining an unknown verification result."""
        source = "complex_code()"
        debugger = SymbolicDebugger(source)

        result = VerificationResult(
            code_id="test",
            status=VerificationStatus.UNKNOWN,
            confidence=0.0,
            violations=[],
            z3_result="unknown",
            z3_model=None,
            duration_seconds=3.0,
        )

        explanation = debugger.explain_failure(result)

        assert "UNKNOWN" in explanation

    def test_explain_failure_timeout(self):
        """Test explaining a timeout."""
        source = "complex_computation()"
        debugger = SymbolicDebugger(source)

        result = VerificationResult(
            code_id="test",
            status=VerificationStatus.TIMEOUT,
            confidence=0.0,
            violations=[],
            z3_result="timeout",
            z3_model=None,
            duration_seconds=10.0,
        )

        explanation = debugger.explain_failure(result)

        assert "TIMEOUT" in explanation or "timeout" in explanation.lower()

    def test_explain_failure_handles_missing_success_rate_in_metadata(self):
        """Metadata without a 'success_rate' key must not crash formatting (regression)."""
        source = "x = 5\nassert x == 999"
        debugger = SymbolicDebugger(source)

        result = VerificationResult(
            code_id="test",
            status=VerificationStatus.FAIL,
            confidence=0.0,
            violations=[],
            z3_result="unsat",
            z3_model=None,
            duration_seconds=0.05,
            metadata={"non_verifiable_nodes": [], "total_queries": 1},
        )

        explanation = debugger.explain_failure(result)

        assert "N/A" in explanation

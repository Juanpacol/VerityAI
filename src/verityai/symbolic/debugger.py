"""Symbolic debugger: map counterexamples to source lines with fix suggestions."""

import ast
import logging
from typing import Optional

from verityai.ontology.models import Counterexample, VerificationResult, VerificationStatus

logger = logging.getLogger(__name__)


class SymbolicDebugger:
    """Map Z3 counterexamples to source code with fix suggestions."""

    def __init__(self, source_code: str):
        """Initialize debugger with source code.

        Args:
            source_code: Python code being verified
        """
        self.source_code = source_code
        self.lines = source_code.split("\n")
        self.line_to_node: dict[int, ast.AST] = {}
        try:
            self.tree = ast.parse(source_code)
            self._build_line_map()
        except SyntaxError:
            # Malformed LLM output reaches this constructor in the normal
            # flow (Orchestrator._build_response always builds an
            # explanation, even for a failed/unparseable attempt) -- it
            # must degrade to "no line mapping available," not crash the
            # whole request. verify_python_snippet already reports this
            # snippet as FAIL with a syntax error in its metadata; this
            # class only needs to not blow up alongside it.
            self.tree = None

    def _build_line_map(self) -> None:
        """Build mapping from line numbers to AST nodes."""
        for node in ast.walk(self.tree):
            if hasattr(node, "lineno"):
                lineno = node.lineno
                if lineno not in self.line_to_node:
                    self.line_to_node[lineno] = node

    def debug(
        self,
        verification_result: VerificationResult,
        rule_name: Optional[str] = None,
    ) -> dict:
        """Generate debug info from verification result.

        Args:
            verification_result: Result from Z3Engine.verify_code()
            rule_name: Name of rule that was violated (optional)

        Returns:
            Dictionary with debug information
        """
        debug_info = {
            "status": verification_result.status.value,
            "confidence": verification_result.confidence,
            "violations": [],
        }

        if verification_result.violations:
            for violation in verification_result.violations:
                debug_entry = self._debug_violation(violation, rule_name)
                debug_info["violations"].append(debug_entry)

        return debug_info

    def _debug_violation(
        self,
        counterexample: Counterexample,
        rule_name: Optional[str] = None,
    ) -> dict:
        """Generate debug info for a single violation.

        Args:
            counterexample: Counterexample that caused violation
            rule_name: Name of violated rule

        Returns:
            Debug entry with source line and fix suggestion
        """
        # Find suspicious line (heuristic: line with array access or comparison)
        suspicious_line = self._find_suspicious_line(counterexample)

        debug_entry = {
            "rule": rule_name or counterexample.rule_id,
            "description": counterexample.description,
            "counterexample_inputs": counterexample.input_values,
            "suspicious_line": suspicious_line,
            "source_code": self._get_context(suspicious_line),
            "fix_suggestions": [],
        }

        # Generate fix suggestions based on violation type
        suggestions = self._generate_fix_suggestions(
            counterexample,
            suspicious_line,
            rule_name,
        )
        debug_entry["fix_suggestions"] = suggestions

        return debug_entry

    def _find_suspicious_line(self, counterexample: Counterexample) -> Optional[int]:
        """Find likely source of violation based on counterexample.

        Args:
            counterexample: Counterexample with input values

        Returns:
            Line number or None
        """
        if counterexample.source_line:
            return counterexample.source_line

        # Heuristic: look for lines with array access or bounds checks
        for lineno, line_text in enumerate(self.lines, start=1):
            line_lower = line_text.lower()

            # Array access pattern: arr[...] or arr[i]
            if "[" in line_lower and "]" in line_lower:
                # Check if counterexample has array-related variables
                for var_name in counterexample.input_values.keys():
                    if var_name.lower() in line_lower or "idx" in var_name.lower():
                        return lineno

            # Comparison pattern: > < >= <=
            if any(op in line_lower for op in [" > ", " < ", " >= ", " <= "]):
                for var_name in counterexample.input_values.keys():
                    if var_name.lower() in line_lower:
                        return lineno

        # Default: return first line with code
        return self._first_executable_line()

    def _first_executable_line(self) -> Optional[int]:
        """Find first line with executable code.

        Returns:
            Line number or None
        """
        for lineno, line_text in enumerate(self.lines, start=1):
            stripped = line_text.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith('"""'):
                return lineno
        return None

    def _get_context(self, line_number: Optional[int], context_lines: int = 3) -> str:
        """Get source code context around a line.

        Args:
            line_number: Line to get context for
            context_lines: Number of lines before/after to include

        Returns:
            Formatted source code with context
        """
        if line_number is None:
            return ""

        start = max(0, line_number - context_lines - 1)
        end = min(len(self.lines), line_number + context_lines)

        context = []
        for i in range(start, end):
            marker = "→ " if (i + 1 == line_number) else "  "
            context.append(f"{marker}{i+1:3d} | {self.lines[i]}")

        return "\n".join(context)

    def _generate_fix_suggestions(
        self,
        counterexample: Counterexample,
        line_number: Optional[int],
        rule_name: Optional[str] = None,
    ) -> list[str]:
        """Generate fix suggestions based on violation type.

        Args:
            counterexample: Counterexample
            line_number: Suspicious line number
            rule_name: Name of violated rule

        Returns:
            List of fix suggestions
        """
        suggestions = []

        rule_name_lower = (rule_name or "").lower()
        inputs = counterexample.input_values

        # Bounds check violations
        if "bound" in rule_name_lower or "index" in rule_name_lower:
            if "idx" in inputs or "index" in inputs:
                idx_val = inputs.get("idx") or inputs.get("index")
                suggestions.append(
                    f"Add bounds check: assert 0 <= idx < len(arr) (found idx={idx_val})"
                )
            suggestions.append("Use safe indexing: arr.get(idx, default) instead of arr[idx]")
            suggestions.append("Check array length before access")

        # Null/None dereference
        if "null" in rule_name_lower or "none" in rule_name_lower:
            suggestions.append("Add null check: if obj is not None: ...")
            suggestions.append("Use optional chaining or default values")

        # SQL injection
        if "sql" in rule_name_lower or "injection" in rule_name_lower:
            suggestions.append("Use parameterized queries: cursor.execute(query, (param,))")
            suggestions.append("Sanitize user input before building SQL")

        # Array invariant violations
        if "sort" in rule_name_lower or "invariant" in rule_name_lower:
            suggestions.append("Verify array is sorted before binary search")
            suggestions.append("Add assertion: assert is_sorted(arr)")

        # Generic suggestions
        if not suggestions:
            suggestions.append("Review preconditions: ensure inputs match specification")
            suggestions.append("Add input validation: assert condition for critical values")
            suggestions.append("Consider loop invariants or explicit bounds checks")

        return suggestions

    def explain_failure(self, verification_result: VerificationResult) -> str:
        """Generate human-readable explanation of verification failure.

        Args:
            verification_result: Result from Z3Engine

        Returns:
            Formatted explanation text
        """
        lines = []

        if verification_result.status == VerificationStatus.PASS:
            return "✓ Verification passed with confidence {:.1%}".format(
                verification_result.confidence
            )

        if verification_result.status == VerificationStatus.FAIL:
            lines.append("✗ Verification FAILED")
            lines.append(f"  Confidence: {verification_result.confidence:.1%}")

            if verification_result.violations:
                lines.append("\nViolations found:")
                for i, violation in enumerate(verification_result.violations, start=1):
                    lines.append(f"\n  {i}. {violation.description}")
                    lines.append(f"     Counterexample inputs: {violation.input_values}")

                    if violation.suggested_fix:
                        lines.append(f"     Suggested fix: {violation.suggested_fix}")

        elif verification_result.status == VerificationStatus.UNKNOWN:
            lines.append("⚠ Verification UNKNOWN (Z3 could not determine)")
            lines.append("  This typically means:")
            lines.append("  - Constraints are too complex for SMT solver")
            lines.append("  - Missing loop invariants or preconditions")
            lines.append("  - Consider adding explicit assertions")

        elif verification_result.status == VerificationStatus.TIMEOUT:
            lines.append("⏱ Verification TIMEOUT")
            lines.append("  The Z3 solver took too long to solve the constraints.")
            lines.append("  Consider:")
            lines.append("  - Simplifying preconditions")
            lines.append("  - Adding loop invariants")
            lines.append("  - Breaking problem into smaller pieces")

        lines.append("\nMetadata:")
        if verification_result.metadata:
            lines.append(f"  Total Z3 queries: {verification_result.metadata.get('total_queries', 'N/A')}")
            lines.append(
                f"  Unknown queries: {verification_result.metadata.get('unknown_queries', 'N/A')}"
            )
            success_rate = verification_result.metadata.get("success_rate")
            success_rate_str = f"{success_rate:.1%}" if isinstance(success_rate, (int, float)) else "N/A"
            lines.append(f"  Success rate: {success_rate_str}")

        return "\n".join(lines)

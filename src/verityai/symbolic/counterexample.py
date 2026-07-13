"""Extract and format counterexamples from Z3 models for human interpretation."""

import logging
from typing import Any, Optional

from verityai.ontology.models import Counterexample

logger = logging.getLogger(__name__)


class CounterexampleGenerator:
    """Convert Z3 model assignments to human-readable counterexamples."""

    def __init__(self, source_code: Optional[str] = None):
        """Initialize counterexample generator.

        Args:
            source_code: Original source code for line mapping (optional)
        """
        self.source_code = source_code
        self.lines = source_code.split("\n") if source_code else []

    def from_z3_model(
        self,
        z3_model: Any,
        rule_id: Optional[str] = None,
        description: str = "Postcondition violated",
    ) -> Counterexample:
        """Convert Z3 model to Counterexample object.

        Args:
            z3_model: Z3 Model from SAT result
            rule_id: ID of rule that was violated (optional)
            description: Human-readable description

        Returns:
            Counterexample object with extracted values
        """
        input_values = self._extract_model_values(z3_model)

        return Counterexample(
            rule_id=rule_id,
            input_values=input_values,
            description=description,
            source_line=None,
            suggested_fix=None,
        )

    def _extract_model_values(self, z3_model: Any) -> dict[str, Any]:
        """Extract concrete variable assignments from Z3 model.

        Args:
            z3_model: Z3 Model object

        Returns:
            Dictionary mapping variable names to concrete Python values
        """
        values = {}

        try:
            for decl in z3_model.decls():
                var_name = decl.name()
                var_value = z3_model[decl]

                # Convert Z3 value to Python
                values[var_name] = self._z3_to_python(var_value)

        except Exception as e:
            logger.warning(f"Failed to extract model values: {e}")
            values["_extraction_error"] = str(e)

        return values

    def _z3_to_python(self, z3_value: Any) -> Any:
        """Convert Z3 value to Python type.

        Args:
            z3_value: Z3 value (Int, Bool, Real, etc.)

        Returns:
            Python-native value (int, bool, float, str)
        """
        # Try integer
        if hasattr(z3_value, "as_long"):
            try:
                return z3_value.as_long()
            except Exception:
                pass

        # Try boolean
        if hasattr(z3_value, "as_bool"):
            try:
                return z3_value.as_bool()
            except Exception:
                pass

        # Try float/fraction
        if hasattr(z3_value, "as_fraction"):
            try:
                frac = z3_value.as_fraction()
                return float(frac)
            except Exception:
                pass

        # Try string
        if hasattr(z3_value, "as_string"):
            try:
                return z3_value.as_string()
            except Exception:
                pass

        # Fallback: string representation
        return str(z3_value)

    def generate_fix_suggestion(
        self,
        counterexample: Counterexample,
        rule_name: str,
    ) -> str:
        """Generate a human-readable fix suggestion based on counterexample.

        Args:
            counterexample: Counterexample that violated the rule
            rule_name: Name of the rule that was violated

        Returns:
            Suggested fix text
        """
        input_str = ", ".join([f"{k}={v}" for k, v in counterexample.input_values.items()])

        suggestion = f"""
Rule violation: {rule_name}
Counterexample inputs: {input_str}

Suggested fix:
- Review boundary conditions for these input values
- Add explicit validation for edge cases
- Consider adding type guards or assertions
"""
        return suggestion.strip()

    def format_for_display(self, counterexample: Counterexample) -> str:
        """Format counterexample for user display.

        Args:
            counterexample: Counterexample object

        Returns:
            Formatted string for display
        """
        lines = [
            f"Counterexample: {counterexample.description}",
            f"Inputs that cause failure:",
        ]

        for key, value in counterexample.input_values.items():
            if not key.startswith("_"):
                lines.append(f"  {key} = {value}")

        if counterexample.source_line:
            lines.append(f"Source line: {counterexample.source_line}")

        if counterexample.suggested_fix:
            lines.append(f"\nSuggested fix:\n{counterexample.suggested_fix}")

        return "\n".join(lines)

    def group_counterexamples(
        self,
        counterexamples: list[Counterexample],
    ) -> dict[str, list[Counterexample]]:
        """Group counterexamples by rule.

        Args:
            counterexamples: List of counterexamples

        Returns:
            Dictionary mapping rule_id to list of counterexamples
        """
        grouped: dict[str, list[Counterexample]] = {}

        for ce in counterexamples:
            rule_id = ce.rule_id or "unknown"
            if rule_id not in grouped:
                grouped[rule_id] = []
            grouped[rule_id].append(ce)

        return grouped

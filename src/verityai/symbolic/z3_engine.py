"""Z3 Theorem Prover wrapper for symbolic verification."""

import logging
import signal
import time
from contextlib import contextmanager
from typing import Any, Optional

from z3 import (
    And,
    Bool,
    Implies,
    Int,
    Not,
    Or,
    Solver,
    sat,
    unsat,
)

from verityai.ontology.models import Counterexample, VerificationResult, VerificationStatus

logger = logging.getLogger(__name__)


class Z3Engine:
    """Wrapper around Z3 Theorem Prover with timeout handling."""

    def __init__(self, timeout_seconds: float = 3.0, max_unknown_allowed: float = 0.2):
        """Initialize Z3 engine.

        Args:
            timeout_seconds: Timeout per solve() call (seconds)
            max_unknown_allowed: Max fraction of queries allowed to return UNKNOWN (0-1)
        """
        self.timeout_seconds = timeout_seconds
        self.max_unknown_allowed = max_unknown_allowed
        self.solver: Optional[Solver] = None
        self.total_queries = 0
        self.unknown_queries = 0

    def _create_solver(self) -> Solver:
        """Create a new Z3 solver with timeout configured."""
        solver = Solver()
        solver.set("timeout", int(self.timeout_seconds * 1000))  # Z3 uses milliseconds
        return solver

    def reset(self) -> None:
        """Reset solver state."""
        self.solver = self._create_solver()
        self.total_queries = 0
        self.unknown_queries = 0

    def add_constraint(self, constraint: Any) -> None:
        """Add a constraint to the solver.

        Args:
            constraint: Z3 expression (should be BoolSort)
        """
        if self.solver is None:
            self.reset()
        self.solver.add(constraint)

    def check_satisfiable(self, assertions: Optional[list[Any]] = None) -> tuple[VerificationStatus, Optional[Any]]:
        """Check if assertions are satisfiable.

        Args:
            assertions: List of Z3 expressions to check (or use solver state)

        Returns:
            (VerificationStatus, Model or None if UNSAT/UNKNOWN)
        """
        if self.solver is None:
            self.reset()

        self.total_queries += 1
        start_time = time.time()

        # Temporarily add assertions if provided
        old_state = None
        if assertions:
            old_state = self.solver.to_smt2()
            for assertion in assertions:
                self.solver.add(assertion)

        try:
            result = self.solver.check()
            elapsed = time.time() - start_time

            if result == sat:
                status = VerificationStatus.PASS
                model = self.solver.model()
            elif result == unsat:
                status = VerificationStatus.FAIL
                model = None
            else:  # UNKNOWN
                status = VerificationStatus.UNKNOWN
                model = None
                self.unknown_queries += 1

            logger.debug(f"Z3 check: {status.value} (elapsed: {elapsed:.2f}s)")
            return status, model

        except Exception as e:
            if "timeout" in str(e).lower():
                logger.warning(f"Z3 timeout after {self.timeout_seconds}s")
                self.unknown_queries += 1
                return VerificationStatus.TIMEOUT, None
            raise

        finally:
            # Restore old state if we added temporary assertions
            if old_state is not None:
                self.solver = self._create_solver()
                # Re-add all original assertions (Z3 doesn't provide simple state restore)
                # For now, we just reset and recreate
                logger.warning("Temporary assertions used; solver state reset")

    def verify_property(
        self,
        property_to_prove: Any,
        assumptions: Optional[list[Any]] = None,
    ) -> tuple[VerificationStatus, Optional[dict[str, Any]]]:
        """Verify a property (postcondition) given assumptions (preconditions).

        Strategy: Prove property by showing NOT(property) is unsatisfiable.

        Args:
            property_to_prove: Z3 expression representing desired property
            assumptions: Z3 expressions representing preconditions

        Returns:
            (VerificationStatus, Counterexample dict or None)
        """
        # Build the negation: we try to find a counterexample to the property
        negation = Not(property_to_prove)

        # Combine assumptions and negation
        assertions = (assumptions or []) + [negation]

        status, model = self.check_satisfiable(assertions)

        counterexample = None
        if status == VerificationStatus.PASS:
            # SAT means we found a counterexample (property is FALSE)
            # Extract concrete values from model
            counterexample = self._extract_counterexample(model)
            status = VerificationStatus.FAIL
        elif status == VerificationStatus.FAIL:
            # UNSAT means property is always TRUE (verified)
            status = VerificationStatus.PASS
        # UNKNOWN/TIMEOUT: return as-is

        return status, counterexample

    def _extract_counterexample(self, model: Any) -> dict[str, Any]:
        """Extract concrete variable assignments from Z3 model.

        Args:
            model: Z3 model (result of SAT)

        Returns:
            Dictionary mapping variable names to concrete values
        """
        counterexample = {}

        try:
            for decl in model.decls():
                var_name = decl.name()
                var_value = model[decl]

                # Convert Z3 value to Python
                if hasattr(var_value, "as_long"):
                    counterexample[var_name] = var_value.as_long()
                elif hasattr(var_value, "as_fraction"):
                    counterexample[var_name] = float(var_value.as_fraction())
                elif hasattr(var_value, "as_string"):
                    counterexample[var_name] = var_value.as_string()
                else:
                    counterexample[var_name] = str(var_value)
        except Exception as e:
            logger.warning(f"Failed to extract counterexample: {e}")
            counterexample["_extraction_error"] = str(e)

        return counterexample

    def verify_code(
        self,
        code_constraints: list[Any],
        postcondition: Any,
        preconditions: Optional[list[Any]] = None,
    ) -> VerificationResult:
        """Verify that code satisfies postcondition given preconditions.

        Args:
            code_constraints: Z3 constraints extracted from code
            postcondition: Z3 expression for desired postcondition
            preconditions: Z3 expressions for input requirements

        Returns:
            VerificationResult with status, confidence, violations
        """
        start_time = time.time()

        # Build full constraint set
        all_constraints = (preconditions or []) + code_constraints

        # Verify postcondition
        status, counterexample_dict = self.verify_property(
            postcondition,
            assumptions=all_constraints,
        )

        duration = time.time() - start_time

        # Build violation if counterexample found
        violations = []
        if counterexample_dict and status == VerificationStatus.FAIL:
            violations.append(
                Counterexample(
                    rule_id=None,  # Will be filled by caller
                    input_values=counterexample_dict,
                    description="Postcondition violated with these inputs",
                )
            )

        # Adjust confidence based on query success rate
        confidence = 1.0
        if self.total_queries > 0:
            success_rate = 1.0 - (self.unknown_queries / self.total_queries)
            confidence = success_rate * 0.95 + 0.05  # Min 0.05 confidence even if all UNKNOWN

        return VerificationResult(
            code_id="",  # Will be filled by caller
            status=status,
            confidence=confidence,
            violations=violations,
            z3_result=status.value,
            z3_model=counterexample_dict,
            duration_seconds=duration,
            metadata={
                "total_queries": self.total_queries,
                "unknown_queries": self.unknown_queries,
                "success_rate": 1.0 - (self.unknown_queries / max(1, self.total_queries)),
            },
        )

    def get_health_check(self) -> dict[str, Any]:
        """Get engine health statistics."""
        return {
            "total_queries": self.total_queries,
            "unknown_queries": self.unknown_queries,
            "success_rate": 1.0 - (self.unknown_queries / max(1, self.total_queries)),
            "timeout_seconds": self.timeout_seconds,
            "status": "healthy" if self.success_rate >= (1.0 - self.max_unknown_allowed) else "degraded",
        }

    @property
    def success_rate(self) -> float:
        """Compute overall success rate."""
        if self.total_queries == 0:
            return 1.0
        return 1.0 - (self.unknown_queries / self.total_queries)

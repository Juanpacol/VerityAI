"""Deductive rule engine for symbolic reasoning (IBM NSTK pattern adapted)."""

import logging
from typing import Any, Optional

from verityai.ontology.models import Rule, VerificationStatus

logger = logging.getLogger(__name__)


class RuleEngine:
    """Deductive reasoning engine: forward chaining with rule application."""

    def __init__(self, max_iterations: int = 10):
        """Initialize rule engine.

        Args:
            max_iterations: Max forward-chaining iterations to prevent infinite loops
        """
        self.rules: list[Rule] = []
        self.max_iterations = max_iterations
        self.facts: set[str] = set()  # Known facts at current reasoning state
        self.inference_trace: list[dict[str, Any]] = []  # Trace of rule applications

    def add_rule(self, rule: Rule) -> None:
        """Add a rule to the knowledge base.

        Args:
            rule: Rule to add (should have formal_spec defining preconditions/postcondition)
        """
        self.rules.append(rule)
        logger.debug(f"Rule added: {rule.name}")

    def add_rules_batch(self, rules: list[Rule]) -> None:
        """Add multiple rules at once.

        Args:
            rules: List of rules to add
        """
        for rule in rules:
            self.add_rule(rule)

    def add_fact(self, fact: str) -> None:
        """Add a known fact to the reasoning state.

        Args:
            fact: Fact string (e.g., "code_has_null_check", "array_is_sorted")
        """
        self.facts.add(fact)

    def reset(self) -> None:
        """Reset reasoning state (clear facts and trace)."""
        self.facts = set()
        self.inference_trace = []

    def infer(self, new_facts: set[str]) -> tuple[set[str], list[dict[str, Any]]]:
        """Forward chain reasoning: apply rules until no new facts can be derived.

        Args:
            new_facts: Initial facts to start reasoning with

        Returns:
            (set of derived facts, trace of rule applications)
        """
        self.reset()
        self.facts = new_facts.copy()
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1
            new_derived = set()

            for rule in self.rules:
                # Check if preconditions are satisfied
                if self._preconditions_met(rule):
                    # Derive postcondition
                    consequence = self._derive_consequence(rule)
                    if consequence and consequence not in self.facts:
                        new_derived.add(consequence)

                        # Log trace
                        self.inference_trace.append({
                            "iteration": iteration,
                            "rule": rule.name,
                            "preconditions_met": list(self.facts),
                            "derived": consequence,
                        })

            # No new facts derived → fixed point reached
            if not new_derived:
                break

            self.facts.update(new_derived)
            logger.debug(f"Iteration {iteration}: derived {len(new_derived)} new facts")

        return self.facts, self.inference_trace

    def _preconditions_met(self, rule: Rule) -> bool:
        """Check if rule preconditions are satisfied by current facts.

        Args:
            rule: Rule to check

        Returns:
            True if all preconditions are in current facts
        """
        if not rule.formal_spec:
            return False

        # Parse preconditions from formal_spec (simple string matching)
        # Format: "PRE: fact1, fact2; POST: consequence" or "PRE: A; POST: B"
        spec = rule.formal_spec

        if "PRE:" not in spec:
            return False

        pre_section = spec.split("PRE:")[1].split(";")[0].strip()
        precondition_facts = [f.strip() for f in pre_section.split(",")]

        # All preconditions must be in current facts
        for precond in precondition_facts:
            if precond and precond not in self.facts:
                return False

        return True

    def _derive_consequence(self, rule: Rule) -> Optional[str]:
        """Extract consequence from rule formal_spec.

        Args:
            rule: Rule to extract consequence from

        Returns:
            Consequence fact string or None
        """
        if not rule.formal_spec:
            return None

        spec = rule.formal_spec
        if "POST:" not in spec:
            return None

        post_section = spec.split("POST:")[1].strip()
        # Simple extraction: first line after POST:
        consequence = post_section.split(";")[0].strip()

        return consequence if consequence else None

    def apply_rule_to_code(
        self,
        rule: Rule,
        code_facts: dict[str, Any],
    ) -> tuple[VerificationStatus, Optional[str]]:
        """Apply a single rule to code facts.

        Args:
            rule: Rule to apply
            code_facts: Dictionary of facts extracted from code (e.g., has_null_check, is_sorted)

        Returns:
            (VerificationStatus, explanation)
        """
        fact_strings = set(code_facts.keys())
        self.reset()
        self.facts = fact_strings

        # Check preconditions
        if not self._preconditions_met(rule):
            return VerificationStatus.UNKNOWN, f"Rule {rule.name} preconditions not met"

        consequence = self._derive_consequence(rule)
        if consequence:
            self.facts.add(consequence)
            return VerificationStatus.PASS, f"Rule {rule.name} applied: {consequence}"

        return VerificationStatus.UNKNOWN, f"No consequence derived from {rule.name}"

    def get_applicable_rules(self, code_facts: dict[str, Any], language: str = "python") -> list[Rule]:
        """Get all rules applicable to code given current facts.

        Args:
            code_facts: Dictionary of code facts
            language: Programming language (python, java, etc.)

        Returns:
            List of applicable rules
        """
        applicable = []
        fact_strings = set(code_facts.keys())

        for rule in self.rules:
            # Filter by language
            if language not in rule.applies_to:
                continue

            # Check preconditions
            self.reset()
            self.facts = fact_strings
            if self._preconditions_met(rule):
                applicable.append(rule)

        return applicable

    def get_inference_trace(self) -> list[dict[str, Any]]:
        """Get the trace of rule applications.

        Returns:
            List of trace entries (iteration, rule, preconditions, derived)
        """
        return self.inference_trace

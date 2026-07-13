"""Ontology module — pure Pydantic models (zero dependencies on neo4j/z3).

This module defines the core data structures used throughout VerityAI.
It has ZERO dependencies on infrastructure (neo4j, z3, llm) — everything else
imports from here, which breaks circular dependency chains.
"""

from .models import (
    Algorithm,
    Counterexample,
    Pattern,
    Rule,
    VerificationResult,
)

__all__ = [
    "Rule",
    "Pattern",
    "Algorithm",
    "VerificationResult",
    "Counterexample",
]

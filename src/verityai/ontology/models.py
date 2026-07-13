"""Core Pydantic models for VerityAI ontology."""

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class VerificationStatus(str, Enum):
    """Result of symbolic verification."""
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    TIMEOUT = "timeout"
    NOT_VERIFIED = "not_verified"


class Rule(BaseModel):
    """Symbolic rule in the Knowledge Graph."""
    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str
    category: str  # e.g., "security", "correctness", "efficiency"
    condition: str  # Description of what the rule checks for
    severity: str  # "critical", "high", "medium", "low", "info"
    applies_to: list[str] = Field(default_factory=list)  # Programming languages
    formal_spec: Optional[str] = None  # Z3 SMT-LIB2 or similar
    examples: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_schema_extra = {
            "example": {
                "name": "no_null_dereference",
                "description": "Ensure no null pointer dereferences",
                "category": "security",
                "condition": "If x is used, x must not be None",
                "severity": "critical",
                "applies_to": ["python", "java", "c++"]
            }
        }


class Counterexample(BaseModel):
    """A concrete input that violates a rule."""
    rule_id: Optional[str] = None
    input_values: dict[str, Any]
    expected_output: Optional[Any] = None
    actual_output: Optional[Any] = None
    description: str
    source_line: Optional[int] = None
    suggested_fix: Optional[str] = None


class Pattern(BaseModel):
    """A verified code pattern."""
    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str
    category: str  # e.g., "algorithm", "utility", "security_wrapper"
    code: str  # Actual code snippet
    language: str  # "python", "java", etc.
    complexity_time: Optional[str] = None  # e.g., "O(n log n)"
    complexity_space: Optional[str] = None  # e.g., "O(1)"
    verified: bool = True
    rules_satisfied: list[UUID] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Algorithm(BaseModel):
    """A canonical algorithm (subset of Pattern)."""
    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str
    code: str
    language: str = "python"
    complexity_time: str  # Required for algorithms
    complexity_space: str  # Required for algorithms
    test_cases: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_schema_extra = {
            "example": {
                "name": "binary_search",
                "description": "Search for target in sorted array",
                "code": "def binary_search(arr, target):\n    left, right = 0, len(arr) - 1\n    while left <= right:\n        mid = (left + right) // 2\n        if arr[mid] == target:\n            return mid\n        elif arr[mid] < target:\n            left = mid + 1\n        else:\n            right = mid - 1\n    return -1",
                "complexity_time": "O(log n)",
                "complexity_space": "O(1)"
            }
        }


class VerificationResult(BaseModel):
    """Result of verifying code against rules."""
    code_id: str  # ID of the code snippet being verified
    status: VerificationStatus
    confidence: float = Field(ge=0.0, le=1.0)  # 0.0 to 1.0
    rules_checked: list[UUID] = Field(default_factory=list)
    violations: list[Counterexample] = Field(default_factory=list)
    z3_result: Optional[str] = None  # "sat", "unsat", "unknown"
    z3_model: Optional[dict[str, Any]] = None  # Satisfying assignment if sat
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReasoningTrace(BaseModel):
    """Full trace of reasoning for code generation + verification."""
    id: UUID = Field(default_factory=uuid4)
    user_prompt: str
    generated_code: str
    attempt_number: int  # Which retry attempt (1-3)
    kg_context: dict[str, Any]  # Rules + patterns injected
    llm_reasoning: str  # Step-by-step reasoning from LLM
    verification_result: Optional[VerificationResult] = None
    failure_reason: Optional[str] = None  # Why it failed (injected for next attempt)
    confidence_score: float = Field(ge=0.0, le=1.0)
    refinement_intent: Optional[str] = None  # Classified intent for refinement turns (e.g. "thread_safety")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class FeedbackType(str, Enum):
    """Production feedback on a generated+verified code attempt."""
    ACCEPT = "accept"
    REJECT = "reject"
    CORRECT = "correct"


class Feedback(BaseModel):
    """User feedback tied back to the ReasoningTrace it responds to.

    Input to the continuous learning loop: REJECT/CORRECT feedback with a
    `reason` can be turned into a candidate KG rule (see
    agent/continuous_learning.py).
    """
    id: UUID = Field(default_factory=uuid4)
    trace_id: UUID
    feedback_type: FeedbackType
    reason: Optional[str] = None  # Why rejected/corrected
    corrected_code: Optional[str] = None  # User-supplied fix, if feedback_type == CORRECT
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GenerationRequest(BaseModel):
    """Request to generate code."""
    prompt: str
    language: str = "python"
    max_attempts: int = 3
    context: dict[str, Any] = Field(default_factory=dict)


class GenerationResponse(BaseModel):
    """Response with generated code + verification proof."""
    code: str
    language: str
    traces: list[ReasoningTrace]  # All attempts
    final_verification: VerificationResult
    confidence: float
    explanation: str  # Human-readable explanation
    status: str  # "success", "partial", "failed"

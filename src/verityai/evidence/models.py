"""Pydantic models for externally-sourced evidence records.

Every claim this project makes about how it compares to the state of the
art (confidence calibration norms, rule-corpus sizes, what SMT solvers can
and can't verify, etc. -- see the T1-T6 research roadmap) should trace back
to one of these records, not to an assumption. Each record carries full
provenance (source, URL, retrieval time, method) so a claim can always be
walked back to where it came from -- the same philosophy as
`ReasoningTrace` for generated code, applied to research evidence instead.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

EvidenceSource = Literal["humaneval", "mbpp", "arxiv", "semgrep", "github_issues", "z3_docs"]
ResearchTopic = Literal["T1", "T2", "T3", "T4", "T5", "T6"]


class ValidationReport(BaseModel):
    """Outcome of deterministic validation (see `evidence.validation`).

    `status` is never inferred silently -- a record that hasn't been
    checked yet is "unchecked", not "valid", so nothing is trusted by
    default.
    """

    status: Literal["valid", "invalid", "stale", "unchecked"] = "unchecked"
    reasons: list[str] = Field(default_factory=list)


class Classification(BaseModel):
    """Outcome of the (optional) LLM classification layer.

    `classified_by` is always stamped, even in degraded paths ("none" when
    no classifier was configured), so nothing is ever mistaken for having
    been reviewed when it wasn't. `classification_reviewed` starts False and
    is only flipped by a human (or the `evidence-auditor` agent's
    recommendation, applied by a human) -- an LLM never marks its own work
    reviewed.
    """

    classified_by: str
    relevance: dict[str, float] = Field(default_factory=dict)
    extracted_claims: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    classification_reviewed: bool = False
    degraded_reason: Optional[str] = None


class EvidenceRecord(BaseModel):
    """One piece of externally-sourced evidence, with full provenance."""

    id: str
    source: EvidenceSource
    source_url: str
    license: Optional[str] = None
    retrieved_at: str  # ISO-8601 UTC
    retrieval_method: Literal["requests", "gh_cli", "file_download"]
    content: dict[str, Any] = Field(default_factory=dict)
    content_hash: str  # sha256 hex digest of the canonical content
    feeds_topics: list[ResearchTopic] = Field(default_factory=list)
    validation: ValidationReport = Field(default_factory=ValidationReport)
    classification: Optional[Classification] = None

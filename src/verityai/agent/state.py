"""Mutable orchestration state for the generate-verify-retry loop."""

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID, uuid4

from verityai.ontology.models import ReasoningTrace, VerificationResult, VerificationStatus


@dataclass
class AgentState:
    """State threaded through one Orchestrator.run() call.

    One AgentState instance corresponds to one user request and lives only
    for the duration of its retry loop (max_attempts). It is not persisted
    directly — Orchestrator converts the accumulated `history` into a
    GenerationResponse at the end of the run.
    """

    user_prompt: str
    language: str = "python"
    max_attempts: int = 3
    attempt_number: int = 0
    current_code: Optional[str] = None
    last_verification: Optional[VerificationResult] = None
    last_failure_reason: Optional[str] = None
    history: list[ReasoningTrace] = field(default_factory=list)
    # Groups every attempt of this run for the reasoning-trace view
    # (GET /runs/{request_id}) -- one AgentState per request, so one id
    # generated once and stamped on every ReasoningTrace it records.
    request_id: UUID = field(default_factory=uuid4)

    @property
    def is_exhausted(self) -> bool:
        """True once max_attempts have been consumed."""
        return self.attempt_number >= self.max_attempts

    @property
    def is_verified(self) -> bool:
        """True if the most recent attempt passed verification."""
        return (
            self.last_verification is not None
            and self.last_verification.status == VerificationStatus.PASS
        )

    def record_attempt(
        self,
        code: str,
        kg_context: dict[str, Any],
        llm_reasoning: str,
        verification_result: VerificationResult,
        confidence_score: float,
        generation_seconds: Optional[float] = None,
        confidence_factors: Optional[dict[str, Any]] = None,
    ) -> ReasoningTrace:
        """Record one generate+verify attempt and update retry-relevant state.

        Args:
            code: Generated code for this attempt
            kg_context: KG rules/patterns injected into the prompt
            llm_reasoning: LLM's explanation text (outside the code block)
            verification_result: Result of running the verifier on `code`
            confidence_score: Weighted confidence for this attempt
            generation_seconds: Wall-clock time for this attempt's generate+verify
            confidence_factors: agent.confidence.explain_confidence() output

        Returns:
            The ReasoningTrace recorded for this attempt
        """
        self.attempt_number += 1
        self.current_code = code
        self.last_verification = verification_result

        trace = ReasoningTrace(
            user_prompt=self.user_prompt,
            generated_code=code,
            attempt_number=self.attempt_number,
            kg_context=kg_context,
            llm_reasoning=llm_reasoning,
            verification_result=verification_result,
            failure_reason=self.last_failure_reason,
            confidence_score=confidence_score,
            request_id=self.request_id,
            generation_seconds=generation_seconds,
            confidence_factors=confidence_factors,
        )
        self.history.append(trace)

        self.last_failure_reason = (
            None
            if verification_result.status == VerificationStatus.PASS
            else self._summarize_failure(verification_result)
        )

        return trace

    def _summarize_failure(self, result: VerificationResult) -> str:
        """Build the short failure-reason string injected into the next retry's prompt."""
        if result.metadata.get("blocked_reason") == "dangerous_code_pattern":
            constructs = ", ".join(f["construct"] for f in result.metadata["security_findings"])
            return f"Generated code was blocked as unsafe (contains: {constructs}). Do not use these constructs."
        if result.violations:
            violation = result.violations[0]
            return f"{violation.description} (counterexample: {violation.input_values})"
        if result.metadata.get("error"):
            return str(result.metadata["error"])
        return f"Verification status: {result.status.value}"

"""Multi-turn conversational session state (interactive refinement mode).

A session opens with one full `Orchestrator.run()` (generate -> verify ->
retry, same as a one-shot request). Every subsequent turn is a *refinement*:
a follow-up instruction like "make it thread-safe" applied to the current
code, generated once (no retry loop) and re-verified incrementally via
`IncrementalVerifier` so only the functions that actually changed pay for a
fresh Z3 check.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID, uuid4

from verityai.agent.confidence import compute_confidence
from verityai.agent.orchestrator import Orchestrator
from verityai.agent.refinement import IncrementalVerifier, RefinementIntent, parse_refinement_intent
from verityai.neural.ollama_client import OllamaGenerationError
from verityai.ontology.models import (
    GenerationRequest,
    GenerationResponse,
    ReasoningTrace,
    VerificationStatus,
)
from verityai.symbolic.debugger import SymbolicDebugger

logger = logging.getLogger(__name__)


@dataclass
class SessionTurn:
    """One turn of a conversation: the user's message and the resulting response."""

    user_message: str
    response: GenerationResponse


@dataclass
class ConversationSession:
    """Ties an Orchestrator + IncrementalVerifier together across turns.

    Attributes:
        id: Session identifier.
        orchestrator: Drives the initial full generate-verify-retry run;
            its `generate_once`/`verify_code` are reused directly for
            refinement turns.
        turns: Ordered history of every turn in this conversation.
    """

    orchestrator: Orchestrator
    id: UUID = field(default_factory=uuid4)
    turns: list[SessionTurn] = field(default_factory=list)
    _incremental_verifier: IncrementalVerifier = field(init=False, repr=False)

    def __post_init__(self):
        self._incremental_verifier = IncrementalVerifier(self.orchestrator.verify_code)

    @property
    def current_code(self) -> str:
        """Code produced by the most recent turn, or "" if the session hasn't started."""
        return self.turns[-1].response.code if self.turns else ""

    def start(self, request: GenerationRequest) -> GenerationResponse:
        """Run the initial full generate-verify-retry loop for this session."""
        response = self.orchestrator.run(request)
        # Prime the incremental cache so the first refine() call doesn't
        # redundantly re-verify functions this full run already checked.
        if response.code:
            self._incremental_verifier.verify(response.code)
        self.turns.append(SessionTurn(user_message=request.prompt, response=response))
        return response

    def refine(self, refinement_prompt: str) -> GenerationResponse:
        """Apply a follow-up instruction to the current code.

        The prompt is first classified into a `RefinementIntent`. Intents
        that only ask about the *existing* result ("show me the proof",
        "explain") are answered from the last turn's already-computed
        verification — no LLM call, no re-verification. Everything else
        generates once (no retry budget — a refinement turn is a quick
        edit, not a fresh search) and incrementally re-verifies only the
        functions whose source changed.

        Raises:
            ValueError: If called before `start()`.
        """
        if not self.turns:
            raise ValueError("Cannot refine before an initial start() call")

        intent = parse_refinement_intent(refinement_prompt)
        if not intent.requires_code_change:
            return self._respond_from_last_turn(refinement_prompt, intent)

        language = self.turns[-1].response.language
        combined_prompt = (
            f"Here is the current code:\n```python\n{self.current_code}\n```\n"
            f"Apply this change: {refinement_prompt}"
        )

        try:
            code, reasoning = self.orchestrator.generate_once(
                combined_prompt, kg_context={}, previous_failure=None
            )
        except OllamaGenerationError as e:
            logger.error(f"Refinement generation failed: {e}")
            return self._build_error_response(refinement_prompt, language, str(e))

        verification_result = self._incremental_verifier.verify(code)
        confidence = compute_confidence(verification_result)

        trace = ReasoningTrace(
            user_prompt=refinement_prompt,
            generated_code=code,
            attempt_number=1,
            kg_context={},
            llm_reasoning=reasoning,
            verification_result=verification_result,
            confidence_score=confidence,
            refinement_intent=intent.intent_type.value,
        )

        if verification_result.status == VerificationStatus.PASS:
            status = "success"
        elif verification_result.status == VerificationStatus.NOT_VERIFIED:
            status = "partial"
        else:
            status = "failed"

        debugger = SymbolicDebugger(code)
        response = GenerationResponse(
            code=code,
            language=language,
            traces=[trace],
            final_verification=verification_result,
            confidence=confidence,
            explanation=debugger.explain_failure(verification_result),
            status=status,
        )
        self.turns.append(SessionTurn(user_message=refinement_prompt, response=response))
        return response

    def _respond_from_last_turn(
        self, refinement_prompt: str, intent: RefinementIntent
    ) -> GenerationResponse:
        """Answer a SHOW_PROOF/EXPLAIN request without calling the LLM or verifier.

        Reuses the last turn's code + verification result — these intents
        ask about work already done, not for a new change.
        """
        last_response = self.turns[-1].response

        trace = ReasoningTrace(
            user_prompt=refinement_prompt,
            generated_code=last_response.code,
            attempt_number=1,
            kg_context={},
            llm_reasoning="",
            verification_result=last_response.final_verification,
            confidence_score=last_response.confidence,
            refinement_intent=intent.intent_type.value,
        )

        response = GenerationResponse(
            code=last_response.code,
            language=last_response.language,
            traces=[trace],
            final_verification=last_response.final_verification,
            confidence=last_response.confidence,
            explanation=last_response.explanation,
            status=last_response.status,
        )
        self.turns.append(SessionTurn(user_message=refinement_prompt, response=response))
        return response

    def _build_error_response(
        self, refinement_prompt: str, language: str, error_message: str
    ) -> GenerationResponse:
        from verityai.ontology.models import VerificationResult

        error_result = VerificationResult(
            code_id="", status=VerificationStatus.FAIL, confidence=0.0,
            metadata={"error": error_message},
        )
        response = GenerationResponse(
            code=self.current_code,
            language=language,
            traces=[],
            final_verification=error_result,
            confidence=0.0,
            explanation=f"Refinement failed: {error_message}",
            status="failed",
        )
        self.turns.append(SessionTurn(user_message=refinement_prompt, response=response))
        return response

"""Generate -> verify -> retry orchestration loop.

MVP verification scope: without a user-supplied formal postcondition or a
matched KG algorithm to check against, "verification" here means the code's
extracted Z3 constraints (including any assert statements it contains) are
internally satisfiable, plus reporting what fraction fell outside
ADR-0001's verifiable subset. This is a real, if limited, check — it will
catch self-contradictory logic and flag unverifiable code honestly rather
than silently passing it. Checking generated code against a specific
postcondition (e.g. when the request matches a known KG algorithm) is
tracked as follow-up work, not implemented in this MVP loop.
"""

import logging
from typing import Optional

from verityai.agent.confidence import compute_confidence
from verityai.agent.state import AgentState
from verityai.kg.client import KGClient
from verityai.neural.ollama_client import OllamaClient, OllamaGenerationError
from verityai.neural.prompt_builder import PromptBuilder
from verityai.ontology.models import (
    GenerationRequest,
    GenerationResponse,
    VerificationResult,
    VerificationStatus,
)
from verityai.symbolic.ast_to_smt import ASTtoSMTConverter
from verityai.symbolic.debugger import SymbolicDebugger
from verityai.symbolic.z3_engine import Z3Engine

logger = logging.getLogger(__name__)


class Orchestrator:
    """Drives the generate -> verify -> (retry up to max_attempts) loop."""

    def __init__(
        self,
        llm_client: OllamaClient,
        kg_client: Optional[KGClient] = None,
        prompt_builder: Optional[PromptBuilder] = None,
        z3_timeout_seconds: float = 3.0,
    ):
        """Initialize orchestrator.

        Args:
            llm_client: Client used to generate code
            kg_client: Optional KG client for rule/pattern context injection.
                If None, generation proceeds without KG context (degraded
                but functional — useful for tests and offline use).
            prompt_builder: Prompt construction with injection hardening.
                Defaults to a new PromptBuilder(strict=False).
            z3_timeout_seconds: Per-query timeout for the verification engine
        """
        self.llm_client = llm_client
        self.kg_client = kg_client
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.z3_timeout_seconds = z3_timeout_seconds

    def run(self, request: GenerationRequest) -> GenerationResponse:
        """Execute the full generate-verify-retry loop for one request.

        Args:
            request: What to generate, language, and max retry budget

        Returns:
            GenerationResponse with the final code, full attempt history,
            and a human-readable explanation of the outcome
        """
        state = AgentState(
            user_prompt=request.prompt,
            language=request.language,
            max_attempts=max(1, request.max_attempts),
        )

        kg_context = self._fetch_kg_context(request)

        while not state.is_exhausted:
            try:
                code, reasoning = self.generate_once(
                    state.user_prompt, kg_context, state.last_failure_reason
                )
            except OllamaGenerationError as e:
                # LLM is unreachable/failing — no point burning remaining
                # attempts on requests that will fail identically.
                logger.error(f"Generation failed, aborting retry loop: {e}")
                return self._build_error_response(state, str(e))

            verification_result = self.verify_code(code)
            confidence = compute_confidence(verification_result)

            state.record_attempt(
                code=code,
                kg_context=kg_context,
                llm_reasoning=reasoning,
                verification_result=verification_result,
                confidence_score=confidence,
            )

            logger.info(
                f"Attempt {state.attempt_number}/{state.max_attempts}: "
                f"status={verification_result.status.value} confidence={confidence:.2f}"
            )

            if state.is_verified:
                break

        return self._build_response(state)

    def _fetch_kg_context(self, request: GenerationRequest) -> dict:
        """Fetch relevant rules from the KG for prompt injection.

        Failures here are non-fatal: generation proceeds with empty context
        rather than blocking the whole request on a KG outage.
        """
        if self.kg_client is None:
            return {}
        try:
            rules = self.kg_client.get_rules_by_category("security", language=request.language)
            rules += self.kg_client.get_rules_by_category("correctness", language=request.language)
            return {
                "rules": [{"name": r.name, "description": r.description} for r in rules],
                "patterns": [],
            }
        except Exception as e:
            logger.warning(f"Failed to fetch KG context, proceeding without it: {e}")
            return {}

    def generate_once(
        self,
        user_prompt: str,
        kg_context: dict,
        previous_failure: Optional[str] = None,
    ) -> tuple[str, str]:
        """Build a prompt and call the LLM once, returning (code, reasoning).

        Public so callers outside the retry loop (e.g. session.py's
        single-turn refinement) can reuse generation without pulling in
        the full AgentState/retry machinery.
        """
        prompt = self.prompt_builder.build_generation_prompt(
            user_request=user_prompt,
            kg_context=kg_context,
            previous_failure=previous_failure,
        )

        raw_response = self.llm_client.generate(prompt)
        return self._split_code_and_reasoning(raw_response)

    def _split_code_and_reasoning(self, raw_response: str) -> tuple[str, str]:
        """Split an LLM response into (code, reasoning) using its first fenced code block.

        Args:
            raw_response: Full LLM response, expected to contain reasoning
                text plus a ```python ... ``` or ``` ... ``` fenced block

        Returns:
            (code, reasoning) — if no fenced block is found, the entire
            response is treated as code with empty reasoning
        """
        for fence in ("```python", "```"):
            if fence in raw_response:
                before, _, rest = raw_response.partition(fence)
                code_block, has_close, after = rest.partition("```")
                if has_close:
                    return code_block.strip(), (before + after).strip()

        return raw_response.strip(), ""

    def verify_code(self, code: str) -> VerificationResult:
        """Run the code through the AST converter + Z3 satisfiability check.

        See module docstring for the MVP scope of what "verification" means
        without a target postcondition. Public so IncrementalVerifier
        (refinement.py) can call it per-function across conversation turns.
        """
        converter = ASTtoSMTConverter(allow_partial=True)

        try:
            constraints, non_verifiable = converter.convert_code(code)
        except SyntaxError as e:
            return VerificationResult(
                code_id="",
                status=VerificationStatus.FAIL,
                confidence=0.0,
                violations=[],
                z3_result=None,
                metadata={"error": f"Syntax error: {e}"},
            )

        if not constraints:
            status = VerificationStatus.NOT_VERIFIED if non_verifiable else VerificationStatus.PASS
            return VerificationResult(
                code_id="",
                status=status,
                confidence=0.3 if non_verifiable else 0.5,
                violations=[],
                z3_result=None,
                metadata={"non_verifiable_nodes": non_verifiable},
            )

        engine = Z3Engine(timeout_seconds=self.z3_timeout_seconds)
        sat_status, _ = engine.check_satisfiable(constraints)

        if sat_status == VerificationStatus.FAIL:
            result_status = VerificationStatus.FAIL
            confidence = 0.0
        elif non_verifiable:
            result_status = VerificationStatus.NOT_VERIFIED
            confidence = engine.success_rate * 0.6
        else:
            result_status = sat_status  # PASS, UNKNOWN, or TIMEOUT
            confidence = engine.success_rate

        return VerificationResult(
            code_id="",
            status=result_status,
            confidence=confidence,
            violations=[],
            z3_result=sat_status.value,
            metadata={
                "non_verifiable_nodes": non_verifiable,
                "total_queries": engine.total_queries,
            },
        )

    def _build_response(self, state: AgentState) -> GenerationResponse:
        """Convert final AgentState into the API-facing GenerationResponse."""
        final_result = state.last_verification or VerificationResult(
            code_id="", status=VerificationStatus.FAIL, confidence=0.0
        )

        if state.is_verified:
            response_status = "success"
        elif final_result.status == VerificationStatus.NOT_VERIFIED:
            response_status = "partial"
        else:
            response_status = "failed"

        debugger = SymbolicDebugger(state.current_code or "")
        explanation = debugger.explain_failure(final_result)

        return GenerationResponse(
            code=state.current_code or "",
            language=state.language,
            traces=state.history,
            final_verification=final_result,
            confidence=state.history[-1].confidence_score if state.history else 0.0,
            explanation=explanation,
            status=response_status,
        )

    def _build_error_response(self, state: AgentState, error_message: str) -> GenerationResponse:
        """Build a response for the case where the LLM itself is unreachable."""
        error_result = VerificationResult(
            code_id="",
            status=VerificationStatus.FAIL,
            confidence=0.0,
            metadata={"error": error_message},
        )
        return GenerationResponse(
            code="",
            language=state.language,
            traces=state.history,
            final_verification=error_result,
            confidence=0.0,
            explanation=f"Code generation failed: {error_message}",
            status="failed",
        )

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
import time
from typing import Any, Optional
from uuid import UUID, uuid4

from verityai.agent import events
from verityai.agent.confidence import explain_confidence
from verityai.agent.event_narration import narrate
from verityai.agent.events import EventEmitter, StageEvent, null_emitter
from verityai.agent.state import AgentState
from verityai.kg.client import KGClient
from verityai.kg.retrieval import HybridRetriever
from verityai.neural.ollama_client import OllamaClient, OllamaGenerationError
from verityai.neural.prompt_builder import PromptBuilder
from verityai.neural.response_parsing import split_code_and_reasoning
from verityai.ontology.models import (
    GenerationRequest,
    GenerationResponse,
    VerificationResult,
    VerificationStatus,
)
from verityai.symbolic.debugger import SymbolicDebugger
from verityai.symbolic.verify import verify_python_snippet

logger = logging.getLogger(__name__)


class Orchestrator:
    """Drives the generate -> verify -> (retry up to max_attempts) loop."""

    def __init__(
        self,
        llm_client: OllamaClient,
        kg_client: Optional[KGClient] = None,
        prompt_builder: Optional[PromptBuilder] = None,
        z3_timeout_seconds: float = 3.0,
        retrieval_strategy: str = "legacy",
        retrieval_top_k: int = 8,
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
            retrieval_strategy: "legacy" (fetch-all by hardcoded category,
                today's behavior) or "hybrid" (kg.retrieval.HybridRetriever,
                ranked by the actual prompt). Defaults to "legacy" until the
                retrieval A/B (docs/PHASE_3_METHODOLOGY.md "Real run #3")
                produces data justifying a flip — see ADR-0003.
            retrieval_top_k: Max rules returned by hybrid retrieval
        """
        self.llm_client = llm_client
        self.kg_client = kg_client
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.z3_timeout_seconds = z3_timeout_seconds
        self.retrieval_strategy = retrieval_strategy
        self.retrieval_top_k = retrieval_top_k

    def _emit(
        self,
        emit: EventEmitter,
        run_id: UUID,
        started: float,
        event_type: str,
        attempt_number: Optional[int] = None,
        **data: Any,
    ) -> None:
        """Build, narrate and hand off one stage event.

        Swallows everything. A broken or slow UI listener must never be able
        to fail a code generation -- the run is the product, the live view is
        a window onto it.
        """
        try:
            emit(
                StageEvent(
                    run_id=run_id,
                    type=event_type,
                    attempt_number=attempt_number,
                    message=narrate(event_type, data),
                    data=data,
                    elapsed_seconds=time.monotonic() - started,
                )
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"Event emitter raised on {event_type}, continuing run: {e}")

    def run(
        self,
        request: GenerationRequest,
        emit: Optional[EventEmitter] = None,
        request_id: Optional[UUID] = None,
    ) -> GenerationResponse:
        """Execute the full generate-verify-retry loop for one request.

        Args:
            request: What to generate, language, and max retry budget
            emit: Optional listener for stage events, so a caller can watch
                the loop progress instead of waiting on the return value.
                Defaults to discarding events, which is why every existing
                caller is unaffected.
            request_id: Optional caller-supplied id for this run. The live
                UI needs a stream URL *before* the run starts, so it
                allocates the id up front and passes it in here; leaving it
                None keeps the previous behaviour of generating one.

        Returns:
            GenerationResponse with the final code, full attempt history,
            and a human-readable explanation of the outcome
        """
        emit = emit or null_emitter
        state = AgentState(
            user_prompt=request.prompt,
            language=request.language,
            max_attempts=max(1, request.max_attempts),
            request_id=request_id or uuid4(),
        )
        run_id = state.request_id
        started = time.monotonic()

        self._emit(
            emit,
            run_id,
            started,
            events.RUN_STARTED,
            prompt=request.prompt,
            language=request.language,
            max_attempts=state.max_attempts,
        )

        self._emit(
            emit, run_id, started, events.RETRIEVAL_STARTED, strategy=self.retrieval_strategy
        )
        kg_context = self._fetch_kg_context(request)
        pattern_similarity = self._extract_pattern_similarity(kg_context)
        self._emit(
            emit,
            run_id,
            started,
            events.RETRIEVAL_COMPLETED,
            **self._retrieval_event_data(kg_context),
        )

        while not state.is_exhausted:
            attempt_started = time.monotonic()
            next_attempt = state.attempt_number + 1
            self._emit(
                emit,
                run_id,
                started,
                events.ATTEMPT_STARTED,
                attempt_number=next_attempt,
                has_retry_context=state.last_failure_reason is not None,
                previous_failure=state.last_failure_reason,
            )
            try:
                code, reasoning = self.generate_once(
                    state.user_prompt, kg_context, state.last_failure_reason
                )
            except OllamaGenerationError as e:
                # LLM is unreachable/failing — no point burning remaining
                # attempts on requests that will fail identically.
                logger.error(f"Generation failed, aborting retry loop: {e}")
                self._emit(
                    emit,
                    run_id,
                    started,
                    events.RUN_FAILED,
                    attempt_number=next_attempt,
                    error=str(e),
                )
                return self._build_error_response(state, str(e))

            self._emit(
                emit,
                run_id,
                started,
                events.GENERATION_COMPLETED,
                attempt_number=next_attempt,
                code=code,
                reasoning=reasoning,
                code_lines=len(code.splitlines()),
            )

            self._emit(
                emit,
                run_id,
                started,
                events.VERIFICATION_STARTED,
                attempt_number=next_attempt,
            )
            verification_result = self.verify_code(code)
            generation_seconds = time.monotonic() - attempt_started
            self._emit(
                emit,
                run_id,
                started,
                events.VERIFICATION_COMPLETED,
                attempt_number=next_attempt,
                **self._verification_event_data(verification_result, code),
            )

            confidence_breakdown = explain_confidence(
                verification_result, pattern_similarity=pattern_similarity
            )
            confidence = confidence_breakdown["total"]
            self._emit(
                emit,
                run_id,
                started,
                events.CONFIDENCE_COMPUTED,
                attempt_number=next_attempt,
                **confidence_breakdown,
            )

            trace = state.record_attempt(
                code=code,
                kg_context=kg_context,
                llm_reasoning=reasoning,
                verification_result=verification_result,
                confidence_score=confidence,
                generation_seconds=generation_seconds,
                confidence_factors=confidence_breakdown,
            )

            logger.info(
                f"Attempt {state.attempt_number}/{state.max_attempts}: "
                f"status={verification_result.status.value} confidence={confidence:.2f}"
            )

            self._emit(
                emit,
                run_id,
                started,
                events.ATTEMPT_COMPLETED,
                attempt_number=state.attempt_number,
                status=verification_result.status.value,
                confidence=confidence,
                generation_seconds=generation_seconds,
                trace_id=str(trace.id),
                # Serialized so the API layer can persist the attempt
                # mid-run without reaching back into AgentState.
                trace=trace.model_dump(mode="json"),
            )

            if state.is_verified:
                break

            if not state.is_exhausted:
                self._emit(
                    emit,
                    run_id,
                    started,
                    events.RETRY_SCHEDULED,
                    next_attempt_number=state.attempt_number + 1,
                    failure_reason=state.last_failure_reason,
                )

        response = self._build_response(state)
        self._emit(
            emit,
            run_id,
            started,
            events.RUN_COMPLETED,
            status=response.status,
            confidence=response.confidence,
            attempt_count=len(state.history),
            explanation=response.explanation,
        )
        return response

    @staticmethod
    def _retrieval_event_data(kg_context: dict) -> dict:
        """Flatten kg_context into JSON-safe fields for a retrieval event."""
        rules = kg_context.get("rules") or []
        retrieval_meta = kg_context.get("retrieval")
        data: dict[str, Any] = {
            "strategy": "legacy",
            "rule_count": len(rules),
            "rules": rules,
        }
        if isinstance(retrieval_meta, dict):
            data.update(
                strategy=retrieval_meta.get("strategy", "hybrid"),
                mode=retrieval_meta.get("mode"),
                degraded_reason=retrieval_meta.get("degraded_reason"),
                top_semantic_similarity=retrieval_meta.get("top_semantic_similarity"),
            )
        return data

    @staticmethod
    def _verification_event_data(result: VerificationResult, code: str) -> dict:
        """Flatten a VerificationResult into JSON-safe fields for an event.

        Counterexamples are resolved here rather than in the API layer
        because `narrate()` runs at emit time and needs them to say "Z3
        found a case where this fails: x=-1" instead of a generic sentence.
        `agent/` may depend on `symbolic/` (see CLAUDE.md), so running the
        debugger here costs nothing structurally.
        """
        non_verifiable = result.metadata.get("non_verifiable_nodes") or []
        counterexamples: list = []
        if result.violations:
            try:
                counterexamples = SymbolicDebugger(code).debug(result).get("violations", [])
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(f"Could not build counterexample detail for event: {e}")
        return {
            "status": result.status.value,
            "solver_confidence": result.confidence,
            "violation_count": len(result.violations),
            "counterexamples": counterexamples,
            "non_verifiable_count": len(non_verifiable) if isinstance(non_verifiable, list) else 0,
            "duration_seconds": result.duration_seconds,
            "verification_result": result.model_dump(mode="json"),
        }

    def _fetch_kg_context(self, request: GenerationRequest) -> dict:
        """Fetch relevant rules from the KG for prompt injection.

        Dispatches on `self.retrieval_strategy`. Failures here are
        non-fatal: generation proceeds with empty context rather than
        blocking the whole request on a KG outage.
        """
        if self.kg_client is None:
            return {}
        try:
            if self.retrieval_strategy == "hybrid":
                return self._fetch_kg_context_hybrid(request)
            return self._fetch_kg_context_legacy(request)
        except Exception as e:
            logger.warning(f"Failed to fetch KG context, proceeding without it: {e}")
            return {}

    def _fetch_kg_context_legacy(self, request: GenerationRequest) -> dict:
        """Fetch-all-by-hardcoded-category, prompt-agnostic (today's default)."""
        assert self.kg_client is not None  # guarded by caller
        rules = self.kg_client.get_rules_by_category("security", language=request.language)
        rules += self.kg_client.get_rules_by_category("correctness", language=request.language)
        return {
            "rules": [{"name": r.name, "description": r.description} for r in rules],
            "patterns": [],
        }

    def _fetch_kg_context_hybrid(self, request: GenerationRequest) -> dict:
        """Rank rules against the actual prompt via HybridRetriever, with provenance.

        `embed_fn` is `getattr(self.llm_client, "embed", None)` rather than
        a hard dependency: FakeLLMClient (used throughout the test suite)
        has no `embed` method, so this transparently degrades to
        lexical-only ranking in tests without any special-casing.
        """
        assert self.kg_client is not None  # guarded by caller
        retriever = HybridRetriever(
            self.kg_client, embed_fn=getattr(self.llm_client, "embed", None)
        )
        result = retriever.retrieve(
            request.prompt, language=request.language, top_k=self.retrieval_top_k
        )
        return {
            "rules": [
                {
                    "name": scored.rule.name,
                    "description": scored.rule.description,
                    "severity": scored.rule.severity,
                    "category": scored.rule.category,
                    "provenance": scored.provenance,
                }
                for scored in result.rules
            ],
            "patterns": [],
            "retrieval": {
                "strategy": "hybrid",
                "mode": result.mode,
                "query": request.prompt,
                "top_k": self.retrieval_top_k,
                "degraded_reason": result.degraded_reason,
                "top_semantic_similarity": result.top_semantic_similarity,
            },
        }

    @staticmethod
    def _extract_pattern_similarity(kg_context: dict) -> float:
        """Pull retrieval's top_semantic_similarity out as confidence input.

        Clamped to [0.0, 1.0] (compute_confidence raises outside that
        range) and defaults to 0.0 — honestly, since a legacy fetch or a
        degraded-to-lexical hybrid fetch never measured any similarity at
        all, 0.0 ("no pattern-similarity signal") is the correct value, not
        a guess.
        """
        retrieval_meta = kg_context.get("retrieval")
        if not isinstance(retrieval_meta, dict):
            return 0.0
        raw = retrieval_meta.get("top_semantic_similarity")
        if not isinstance(raw, (int, float)):
            return 0.0
        return max(0.0, min(1.0, float(raw)))

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
        """Thin wrapper over neural.response_parsing.split_code_and_reasoning.

        Kept as an instance method for existing test/call-site compatibility;
        evaluation/baselines.py calls the module-level function directly so
        it doesn't need an Orchestrator instance just to parse text.
        """
        return split_code_and_reasoning(raw_response)

    def verify_code(self, code: str) -> VerificationResult:
        """Run the code through the AST converter + Z3 satisfiability check.

        See module docstring for the MVP scope of what "verification" means
        without a target postcondition. Public so IncrementalVerifier
        (refinement.py) can call it per-function across conversation turns.
        """
        return verify_python_snippet(code, timeout_seconds=self.z3_timeout_seconds)

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
            request_id=state.request_id,
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
            request_id=state.request_id,
        )

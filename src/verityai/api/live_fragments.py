"""Turn stage events into rendered HTML panels for the live view.

Two jobs, both of which have to happen in the API layer:

1. **Rendering.** Panels are server-rendered by reusing `run_view`'s
   existing renderers rather than reimplementing them in JavaScript. The Z3
   panel calls `SymbolicDebugger`, which has no client-side equivalent, and
   forking the confidence bar into JS would let it drift from the fixed
   factor ordering `run_view`'s docstring pins down. Mid-run there is no
   persisted `ReasoningTrace` yet, so each panel is rendered from a
   *synthetic* trace assembled out of the event's own data.

2. **T5 condition masking.** The study manipulation (which panel is
   visible) must be enforced here, not with `display:none` in the browser
   -- a participant who opens dev tools would otherwise see the panel they
   were not supposed to see, silently corrupting their data point. So for
   conditions A and B the suppressed panel's HTML *and* its underlying
   numbers are stripped from the event before it ever reaches the client,
   and its narration is replaced with a neutral sentence so no channel
   leaks what was hidden. See docs/T5_HUMAN_EVAL_PROTOCOL.md.
"""

from typing import Any, Optional
from uuid import uuid4

from verityai.agent import events
from verityai.agent.events import StageEvent
from verityai.api.run_view import (
    render_confidence_fragment,
    render_retrieval_fragment,
    render_z3_fragment,
)
from verityai.ontology.models import ReasoningTrace, VerificationResult

# T5 conditions. See the protocol doc: the point of A and B is to find out
# which panel is load-bearing for trust and which is decorative.
CONDITION_SCORE_ONLY = "A"  # confidence + code; no Z3 panel, no retrieval
CONDITION_Z3_ONLY = "B"  # Z3 panel + code; no confidence bar, no retrieval
CONDITION_EVERYTHING = "C"  # the full view, as production renders it

_SUPPRESSED_MESSAGES = {
    events.RETRIEVAL_COMPLETED: "Knowledge graph retrieval completed.",
    events.VERIFICATION_COMPLETED: "Verification step completed.",
    events.CONFIDENCE_COMPUTED: "Confidence score computed.",
}


def _synthetic_trace(
    generated_code: str = "",
    kg_context: Optional[dict] = None,
    verification_result: Optional[VerificationResult] = None,
    confidence_factors: Optional[dict] = None,
) -> ReasoningTrace:
    """Build a throwaway trace just to feed run_view's renderers mid-run.

    Never persisted and never returned to a caller -- the real trace is
    written by the orchestrator at the end of each attempt.
    """
    return ReasoningTrace(
        id=uuid4(),
        user_prompt="",
        generated_code=generated_code,
        attempt_number=1,
        kg_context=kg_context or {},
        llm_reasoning="",
        verification_result=verification_result,
        confidence_score=0.0,
        confidence_factors=confidence_factors,
    )


def build_html(event: StageEvent, ctx: dict[str, Any]) -> Optional[str]:
    """Render the panel for this event, or None if it has no panel.

    `ctx` is a small mutable dict scoped to one run, owned by the emitter
    closure in api/rest.py. It carries state across events -- notably the
    generated code, which `verification_completed` needs to render a Z3
    counterexample but which only appears on the earlier
    `generation_completed` event.
    """
    if event.type == events.GENERATION_COMPLETED:
        ctx["code"] = event.data.get("code", "")
        return None

    if event.type == events.RETRIEVAL_COMPLETED:
        kg_context: dict[str, Any] = {"rules": event.data.get("rules") or []}
        if event.data.get("strategy") == "hybrid":
            kg_context["retrieval"] = {
                "strategy": "hybrid",
                "mode": event.data.get("mode"),
                "degraded_reason": event.data.get("degraded_reason"),
                "top_semantic_similarity": event.data.get("top_semantic_similarity"),
            }
        ctx["kg_context"] = kg_context
        return render_retrieval_fragment(_synthetic_trace(kg_context=kg_context))

    if event.type == events.VERIFICATION_COMPLETED:
        raw_result = event.data.get("verification_result")
        if not raw_result:
            return None
        try:
            result = VerificationResult.model_validate(raw_result)
        except Exception:
            return None
        return render_z3_fragment(
            _synthetic_trace(generated_code=ctx.get("code", ""), verification_result=result)
        )

    if event.type == events.CONFIDENCE_COMPUTED:
        factors = {
            "total": event.data.get("total", 0.0),
            "components": event.data.get("components") or {},
            "weights": event.data.get("weights") or {},
        }
        return render_confidence_fragment(_synthetic_trace(confidence_factors=factors))

    return None


def apply_condition(event: StageEvent, condition: str) -> None:
    """Strip whatever the T5 condition says this participant must not see.

    Mutates the event in place, before it is buffered. Removes the rendered
    HTML, the underlying values in `data`, and the narration -- all three,
    because leaving any one of them would let a curious participant recover
    the hidden panel from the network tab.
    """
    if condition == CONDITION_EVERYTHING:
        return

    if condition == CONDITION_SCORE_ONLY:
        suppressed = {events.RETRIEVAL_COMPLETED, events.VERIFICATION_COMPLETED}
    elif condition == CONDITION_Z3_ONLY:
        suppressed = {events.RETRIEVAL_COMPLETED, events.CONFIDENCE_COMPUTED}
    else:
        return

    if event.type not in suppressed:
        return

    event.html = None
    event.message = _SUPPRESSED_MESSAGES.get(event.type, "Step completed.")
    # Keep only what the client needs for the stepper -- never the values
    # that would reconstruct the hidden panel.
    event.data = {"suppressed": True}

"""Plain-language narration for pipeline stage events.

Every sentence a user reads in the live view comes from a fixed template in
this module. Deliberately NOT LLM-generated: the whole premise of VerityAI
is that what it shows you is checkable, and a narrator model would produce
fluent text with no guarantee it matches what the pipeline actually did.
Templates are pure functions of the event's `data` -- no I/O, no imports
from `neural/` or `symbolic/`.

The honesty rule that constrains the wording here: NOT_VERIFIED must never
read as anything resembling a pass. It means no proof was attempted, which
is a different and weaker claim than "verified correct" -- see ADR-0001 on
the verifiable Python subset.

Templates take the event's `data` dict and are defensive about missing
keys: an unknown event type or a missing field produces a bland fallback
sentence, never an exception. `narrate()` is called from inside the
orchestrator's exception-swallowing emitter, so a raise here would silently
drop the event rather than surface a bug.
"""

from typing import Any, Callable, Optional

from verityai.agent import events


def _fmt_inputs(inputs: Any) -> str:
    """Render a counterexample's input assignment as `x=-1, y=0`."""
    if not isinstance(inputs, dict) or not inputs:
        return ""
    return ", ".join(f"{key}={value}" for key, value in inputs.items())


def _run_started(data: dict[str, Any]) -> str:
    attempts = data.get("max_attempts", 3)
    language = data.get("language", "python")
    return (
        f"Starting a new run: generating {language} code, with up to "
        f"{attempts} verification attempt(s)."
    )


def _retrieval_started(data: dict[str, Any]) -> str:
    if data.get("strategy") == "hybrid":
        return "Searching the knowledge graph for rules relevant to your prompt..."
    return "Loading rules from the knowledge graph..."


def _retrieval_completed(data: dict[str, Any]) -> str:
    count = data.get("rule_count", 0)
    if data.get("strategy") != "hybrid":
        # Legacy fetch-all is prompt-agnostic; saying "relevant" would
        # overstate what it did.
        return (
            f"Loaded {count} security and correctness rule(s) from the knowledge "
            f"graph. These were fetched by category, not ranked against your prompt."
        )

    degraded = data.get("degraded_reason")
    if degraded:
        return (
            f"Searched the knowledge graph and found {count} rule(s). Semantic "
            f"ranking was unavailable ({degraded}), so these were ranked by "
            f"keyword overlap only."
        )

    similarity = data.get("top_semantic_similarity")
    if isinstance(similarity, (int, float)):
        return (
            f"Searched the knowledge graph and found {count} rule(s) matching your "
            f"prompt (hybrid ranking, closest match scored {float(similarity):.2f})."
        )
    return f"Searched the knowledge graph and found {count} rule(s) matching your prompt."


def _attempt_started(data: dict[str, Any]) -> str:
    number = data.get("attempt_number", 1)
    if data.get("has_retry_context"):
        return (
            f"Attempt {number}: asking the model again, this time with the previous "
            f"failure included in the prompt."
        )
    return f"Attempt {number}: asking the model to generate code with the retrieved rules in context."


def _generation_completed(data: dict[str, Any]) -> str:
    lines = data.get("code_lines")
    if isinstance(lines, int):
        return f"The model returned {lines} line(s) of code. Handing it to the verifier."
    return "The model returned code. Handing it to the verifier."


def _verification_started(data: dict[str, Any]) -> str:
    return "Translating the code to SMT constraints and asking Z3 to check them..."


def _verification_completed(data: dict[str, Any]) -> str:
    status = str(data.get("status", "")).lower()

    if status == "fail":
        counterexamples = data.get("counterexamples") or []
        for violation in counterexamples:
            if not isinstance(violation, dict):
                continue
            rendered = _fmt_inputs(violation.get("counterexample_inputs"))
            if rendered:
                rule = violation.get("rule")
                suffix = f" (rule: {rule})" if rule else ""
                return f"Z3 found a case where this fails: {rendered}.{suffix}"
        return (
            "Z3 could not satisfy the code's own constraints -- the logic is "
            "self-contradictory."
        )

    if status == "not_verified":
        count = data.get("non_verifiable_count")
        detail = (
            f" {count} construct(s) fall outside it."
            if isinstance(count, int) and count > 0
            else ""
        )
        # Never phrase this as success -- no proof was attempted.
        return (
            f"Z3 could not check this code: parts of it are outside the verifiable "
            f"subset (see ADR-0001).{detail} This is NOT a pass -- it means no proof "
            f"was attempted."
        )

    if status == "pass":
        return "Z3 checked the constraints and found no counterexample."

    if status in ("timeout", "unknown"):
        return (
            f"Z3 returned '{status}' -- it neither proved nor disproved the code "
            f"within the time budget. Treat this as unverified, not as a pass."
        )

    return "Verification step completed."


def _confidence_computed(data: dict[str, Any]) -> str:
    components = data.get("components")
    total = data.get("total")
    if not isinstance(components, dict) or not isinstance(total, (int, float)):
        return "Confidence score computed."
    weights = data.get("weights") or {}

    parts = []
    for key in ("verification", "pattern_similarity", "complexity", "test_coverage"):
        component = components.get(key)
        weight = weights.get(key)
        if isinstance(component, (int, float)) and isinstance(weight, (int, float)):
            label = key.replace("_", " ")
            parts.append(f"{label} {float(component):.0%}x{float(weight):.0%}")
    if not parts:
        return f"Confidence {float(total):.0%}."
    return f"Confidence {float(total):.0%} = " + " + ".join(parts) + "."


def _attempt_completed(data: dict[str, Any]) -> str:
    number = data.get("attempt_number", 1)
    status = str(data.get("status", "unknown"))
    seconds = data.get("generation_seconds")
    timing = f" in {float(seconds):.1f}s" if isinstance(seconds, (int, float)) else ""
    return f"Attempt {number} finished{timing} with status '{status}'."


def _retry_scheduled(data: dict[str, Any]) -> str:
    number = data.get("next_attempt_number", "?")
    reason = data.get("failure_reason")
    if reason:
        return (
            f"Retrying (attempt {number}) with the failure fed back into the prompt: {reason}"
        )
    return f"Retrying (attempt {number})."


def _run_completed(data: dict[str, Any]) -> str:
    status = str(data.get("status", "unknown"))
    attempts = data.get("attempt_count")
    suffix = f" after {attempts} attempt(s)" if isinstance(attempts, int) else ""
    return f"Run finished{suffix} with status '{status}'."


def _run_failed(data: dict[str, Any]) -> str:
    error = data.get("error") or "unknown error"
    return f"Run aborted: {error}"


_TEMPLATES: dict[str, Callable[[dict[str, Any]], str]] = {
    events.RUN_STARTED: _run_started,
    events.RETRIEVAL_STARTED: _retrieval_started,
    events.RETRIEVAL_COMPLETED: _retrieval_completed,
    events.ATTEMPT_STARTED: _attempt_started,
    events.GENERATION_COMPLETED: _generation_completed,
    events.VERIFICATION_STARTED: _verification_started,
    events.VERIFICATION_COMPLETED: _verification_completed,
    events.CONFIDENCE_COMPUTED: _confidence_computed,
    events.ATTEMPT_COMPLETED: _attempt_completed,
    events.RETRY_SCHEDULED: _retry_scheduled,
    events.RUN_COMPLETED: _run_completed,
    events.RUN_FAILED: _run_failed,
}


def narrate(event_type: str, data: Optional[dict[str, Any]] = None) -> str:
    """Return a plain-language sentence describing one pipeline event.

    Never raises: an unknown event type, a missing key, or a template bug
    all degrade to a bland but accurate fallback rather than breaking the
    run that is emitting the event.
    """
    template = _TEMPLATES.get(event_type)
    if template is None:
        return event_type.replace("_", " ").capitalize() + "."
    try:
        return template(data or {})
    except Exception:
        return event_type.replace("_", " ").capitalize() + "."

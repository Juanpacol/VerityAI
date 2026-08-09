"""Stage-level events emitted during one Orchestrator.run() call.

These are *transport* objects with a lifetime of a single run -- they exist
so a UI can watch the generate-verify-retry loop happen instead of staring
at a blank page for the 65-125s a real run takes. They are deliberately NOT
in `ontology/`: nothing here is persisted domain state (the persisted record
of a run is still `ReasoningTrace`), and putting them in `agent/` keeps the
dependency graph in CLAUDE.md intact -- `agent/` already depends on
`ontology/`, and the only emitter call sites are in `agent/orchestrator.py`.

Two rules that the rest of the codebase relies on:

1. `data` must be strictly JSON-serializable. The API layer calls
   `model_dump_json()` on every event to write it into an SSE frame, and
   that call happens inside an exception-swallowing emitter -- so a stray
   `UUID` or Pydantic model in `data` would not raise loudly, it would make
   the event vanish. Serialize with `model_dump(mode="json")` / `str()`
   before putting anything non-primitive in `data`.
2. `html` is filled in by the API layer (`api/live_fragments.py`), never by
   `agent/`. Rendering here would mean `agent/` importing `api/`, which
   inverts the dependency graph.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# --- Event types ---------------------------------------------------------
# Emitted by agent/orchestrator.py, in roughly this order. `attempt_*`,
# `generation_*`, `verification_*` and `confidence_computed` repeat once per
# retry attempt.
RUN_STARTED = "run_started"
RETRIEVAL_STARTED = "retrieval_started"
RETRIEVAL_COMPLETED = "retrieval_completed"
ATTEMPT_STARTED = "attempt_started"
GENERATION_COMPLETED = "generation_completed"
VERIFICATION_STARTED = "verification_started"
VERIFICATION_COMPLETED = "verification_completed"
CONFIDENCE_COMPUTED = "confidence_computed"
ATTEMPT_COMPLETED = "attempt_completed"
RETRY_SCHEDULED = "retry_scheduled"
RUN_COMPLETED = "run_completed"
RUN_FAILED = "run_failed"

# Emitted by the SSE layer only (api/rest.py), never by the orchestrator --
# it exists to keep the connection alive through proxies during the long
# silent stretch of an LLM generation.
HEARTBEAT = "heartbeat"

EVENT_TYPES: frozenset = frozenset(
    {
        RUN_STARTED,
        RETRIEVAL_STARTED,
        RETRIEVAL_COMPLETED,
        ATTEMPT_STARTED,
        GENERATION_COMPLETED,
        VERIFICATION_STARTED,
        VERIFICATION_COMPLETED,
        CONFIDENCE_COMPUTED,
        ATTEMPT_COMPLETED,
        RETRY_SCHEDULED,
        RUN_COMPLETED,
        RUN_FAILED,
        HEARTBEAT,
    }
)


class StageEvent(BaseModel):
    """One observable step of the neuro-symbolic pipeline."""

    run_id: UUID
    # Assigned by LiveRunRegistry.append(), not by the emitter -- the
    # registry owns ordering because it is the only thing holding a lock
    # across concurrent runs. It doubles as the SSE `id:` field, which is
    # what makes EventSource's Last-Event-ID reconnect work.
    sequence: int = 0
    type: str
    attempt_number: int | None = None
    # Human-readable, from agent/event_narration.py. Deterministic
    # templates only -- never LLM-generated text.
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    # Server-rendered HTML fragment, attached by api/live_fragments.py.
    # None for events that have no panel of their own.
    html: str | None = None
    elapsed_seconds: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)


# An emitter takes an event and does something with it (enqueue, log, drop).
# It must never raise -- see Orchestrator._emit, which wraps calls in a
# blanket except so a broken UI can never fail a code generation.
EventEmitter = Callable[[StageEvent], None]


def null_emitter(event: StageEvent) -> None:
    """Default emitter: discard the event.

    Lets `Orchestrator.run()` call `emit(...)` unconditionally without
    every existing caller having to pass something.
    """
    return None

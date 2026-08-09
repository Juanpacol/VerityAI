"""In-memory registry of in-flight live runs.

Bridges a synchronous `Orchestrator.run()` executing on a worker thread to
an async SSE response. The mechanism is deliberately boring: the emitter
appends events under a lock and sets a `threading.Event`; the SSE generator
waits on that event with a short timeout and drains whatever is new. No
`BlockingPortal`, no `run_coroutine_threadsafe`, no asyncio primitives
touched from the worker thread -- the sync side stays completely ignorant
of the event loop.

Every event a run has emitted is kept in a bounded buffer, which is what
makes the two-call handshake safe: `POST /live/runs` returns a `run_id`
immediately and starts the run, and an `EventSource` that connects 300ms
later replays events 0..n before tailing. It is also how EventSource's
native `Last-Event-ID` reconnect works with no extra machinery.

This is per-process state, like `api/rate_limit.py`'s counters. That is
fine for the single-instance deployment this project targets; a
multi-worker deployment would need the events in Redis instead.
"""

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional
from uuid import UUID, uuid4

from verityai.agent.events import StageEvent

# A run emits ~10 events per attempt, so 500 is far more than any 3-attempt
# run produces. The cap exists to bound a pathological run, not to trim
# normal ones -- if it ever engages, `events_since` will report a gap.
MAX_EVENTS_PER_RUN = 500

# Finished runs stay queryable for a while so a participant who reloads the
# page (or submits the study questionnaire late) still resolves their run.
FINISHED_TTL_SECONDS = 600.0
# Backstop for a run whose thread wedged and never called finish().
HARD_TTL_SECONDS = 1800.0

VALID_CONDITIONS = ("A", "B", "C")


@dataclass
class LiveRun:
    """One in-flight (or recently finished) pipeline run."""

    run_id: UUID
    # T5 panel-masking condition, assigned server-side. See
    # docs/T5_HUMAN_EVAL_PROTOCOL.md: A = score only, B = Z3 only,
    # C = everything.
    condition: str
    created_at: float
    events: Deque[StageEvent] = field(
        default_factory=lambda: deque(maxlen=MAX_EVENTS_PER_RUN)
    )
    # Set on every append so the SSE generator can wake immediately instead
    # of waiting out its poll interval.
    tick: threading.Event = field(default_factory=threading.Event)
    next_sequence: int = 1
    finished: bool = False
    finished_at: Optional[float] = None
    error: Optional[str] = None


class LiveRunRegistry:
    """Thread-safe store of live runs and their event buffers."""

    def __init__(self) -> None:
        self._runs: dict[UUID, LiveRun] = {}
        self._lock = threading.Lock()

    def create(self, condition: str, run_id: Optional[UUID] = None) -> LiveRun:
        """Register a new run. Sweeps expired entries as a side effect."""
        if condition not in VALID_CONDITIONS:
            raise ValueError(f"condition must be one of {VALID_CONDITIONS}, got {condition!r}")
        self.sweep()
        run = LiveRun(run_id=run_id or uuid4(), condition=condition, created_at=time.monotonic())
        with self._lock:
            self._runs[run.run_id] = run
        return run

    def get(self, run_id: UUID) -> Optional[LiveRun]:
        with self._lock:
            return self._runs.get(run_id)

    def append(self, run_id: UUID, event: StageEvent) -> None:
        """Stamp the event with the next sequence number and buffer it.

        Sequence numbers are assigned here, not by the emitter, because the
        registry is the only component holding a lock across concurrent
        runs. They double as the SSE `id:` field.
        """
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            event.sequence = run.next_sequence
            run.next_sequence += 1
            run.events.append(event)
            run.tick.set()

    def finish(self, run_id: UUID, error: Optional[str] = None) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            run.finished = True
            run.finished_at = time.monotonic()
            run.error = error
            run.tick.set()

    def events_since(self, run_id: UUID, after_sequence: int) -> list[StageEvent]:
        """Return buffered events with `sequence > after_sequence`, in order."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return []
            return [event for event in run.events if event.sequence > after_sequence]

    def active_count(self) -> int:
        """How many runs are still executing. The only backpressure on Ollama."""
        with self._lock:
            return sum(1 for run in self._runs.values() if not run.finished)

    def sweep(self, now: Optional[float] = None) -> int:
        """Evict expired runs. Returns how many were dropped."""
        current = time.monotonic() if now is None else now
        with self._lock:
            expired = [
                run_id
                for run_id, run in self._runs.items()
                if (
                    run.finished
                    and run.finished_at is not None
                    and current - run.finished_at > FINISHED_TTL_SECONDS
                )
                or current - run.created_at > HARD_TTL_SECONDS
            ]
            for run_id in expired:
                del self._runs[run_id]
        return len(expired)


_registry = LiveRunRegistry()


def get_live_run_registry() -> LiveRunRegistry:
    """FastAPI dependency. Overridable in tests via app.dependency_overrides."""
    return _registry


def reset_live_run_state() -> None:
    """Test-only helper, mirroring rate_limit.reset_rate_limit_state()."""
    global _registry
    _registry = LiveRunRegistry()

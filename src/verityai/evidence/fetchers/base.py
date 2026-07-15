"""Shared fetcher infrastructure: politeness rate limiting, crash-resilient
checkpointing, and a uniform result shape.

The checkpointing rationale mirrors `scripts/run_retrieval_ab.py`'s lesson
from the qwen3:8b cross-model run that crashed partway through and lost all
progress: fetchers that iterate many items (HumanEval + MBPP is over 1,100
problems combined) mark each item done as they go, so a re-run after a
crash or Ctrl-C skips everything already completed instead of starting over.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from verityai.evidence.models import EvidenceRecord


class RateLimiter:
    """Enforces a minimum interval between successive calls to `wait()`.

    `time_fn`/`sleep_fn` are injectable so tests can drive this
    deterministically without real wall-clock delays.
    """

    def __init__(
        self,
        min_interval_seconds: float,
        sleep_fn: Optional[Callable[[float], None]] = None,
        time_fn: Optional[Callable[[], float]] = None,
    ):
        self.min_interval_seconds = min_interval_seconds
        self._sleep_fn = sleep_fn or time.sleep
        self._time_fn = time_fn or time.monotonic
        self._last_call: Optional[float] = None

    def wait(self) -> None:
        now = self._time_fn()
        if self._last_call is not None:
            elapsed = now - self._last_call
            remaining = self.min_interval_seconds - elapsed
            if remaining > 0:
                self._sleep_fn(remaining)
                now = now + remaining
        self._last_call = now


class Checkpoint:
    """Tracks which item keys have already been processed, persisted as JSON
    so a fetcher can resume after a crash instead of re-fetching everything.
    """

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {"done": []}
        state: dict = json.loads(self.path.read_text())
        state.setdefault("done", [])
        return state

    def is_done(self, key: str) -> bool:
        return key in self.load()["done"]

    def mark_done(self, key: str) -> None:
        state = self.load()
        if key not in state["done"]:
            state["done"].append(key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state, indent=2, sort_keys=True))


@dataclass
class FetchResult:
    """Outcome of a fetch run. `errors` is populated instead of raising --
    a run that fetched 40/50 items reports 10 failures loudly and still
    returns the 40 it got.
    """

    records: list[EvidenceRecord] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

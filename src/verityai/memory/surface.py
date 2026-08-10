"""Candidate context items sourced from memory, for a proactive surfacing pass.

Lives in `memory/`, not `context/`, because CLAUDE.md's dependency rule
says `context/` must never import `memory/` — ranking a context and
persisting a decision are independent operations, and an adaptive
"decide what to push" pre-pass (`context/adaptive.py`, ADR-0025) still
needs *something* to source candidates from. This module is that source:
it turns active memory records into `ContextItem`s, and `adaptive.py` hands
the result to the existing `ContextPipeline.run` unchanged. Nothing here
ranks, prunes, or decides a budget — that stays in `context/`.
"""

import hashlib

from verityai.context.tokenizer import TokenCounter
from verityai.core.models import ContextItem, ItemKind
from verityai.memory.store import MemoryStore


# Local, not `context/classify.py`'s `content_hash` -- importing it would
# add a `memory -> context.classify` edge the dependency table does not
# declare (only `memory -> context.tokenizer` is, for handoff's token
# budget). `ContextItem.content_hash` only needs to be a stable identifier
# for dedup, not byte-identical to classify.py's own hashing scheme.
def _content_hash(text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def candidates_for(
    store: MemoryStore,
    task: str,
    counter: TokenCounter | None = None,
) -> list[ContextItem]:
    """Active decisions, constraints, discoveries, and unresolved failures,
    as `ContextItem`s -- candidates for a proactive surfacing pass to offer
    `context/adaptive.py::select`.

    Every item is `ItemKind.MEMORY`, which `classify.py:230-231` already
    classifies `CRITICAL` unconditionally -- these records exist specifically
    because they must not be silently dropped, so this reuses that existing
    protection rather than inventing a new one. `task` is accepted for a
    future ranking pass to use; this function itself does not rank or filter
    by relevance, only converts what memory has into the shape `context/`
    already knows how to measure and protect.
    """
    counter = counter or TokenCounter()

    records: list[tuple[str, str]] = []
    for decision in store.decisions():
        records.append((f"decision: {decision.statement}", str(decision.id)))
    for constraint in store.constraints():
        marker = "hard constraint" if constraint.hard else "soft constraint"
        records.append((f"{marker}: {constraint.statement}", str(constraint.id)))
    for discovery in store.discoveries():
        records.append((f"discovery: {discovery.statement}", str(discovery.id)))
    for failure in store.failures(include_resolved=False):
        records.append((f"already tried, did not work: {failure.attempted}", str(failure.id)))

    items: list[ContextItem] = []
    for index, (text, record_id) in enumerate(records):
        count = counter.count(text)
        items.append(
            ContextItem(
                kind=ItemKind.MEMORY,
                content=text,
                token_count=count.tokens,
                token_method=count.method,
                original_index=index,
                content_hash=_content_hash(text),
                metadata={"task": task, "record_id": record_id},
            )
        )
    return items

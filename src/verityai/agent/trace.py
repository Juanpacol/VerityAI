"""Reasoning trace persistence (Postgres) + JSON serialization for API/CLI.

`ReasoningTrace` (ontology/models.py) is the in-memory Pydantic model built
during `Orchestrator.run()`. This module is the write/read side: it flattens
that model into a relational row so traces survive past the request and can
back a future `GET /trace/{id}` API endpoint and the compliance/audit
reports planned for Phase 4.
"""

import json
import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from verityai.db.base import Base
from verityai.ontology.models import ReasoningTrace, VerificationResult

logger = logging.getLogger(__name__)


class TraceRecord(Base):
    """Relational row for one ReasoningTrace (one generate+verify attempt)."""

    __tablename__ = "reasoning_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_prompt: Mapped[str] = mapped_column(Text)
    generated_code: Mapped[str] = mapped_column(Text)
    attempt_number: Mapped[int] = mapped_column(Integer)
    kg_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    llm_reasoning: Mapped[str] = mapped_column(Text, default="")
    verification_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    verification_result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class TraceStore:
    """Persists and retrieves ReasoningTrace rows.

    Takes an injected SQLAlchemy `Session`, consistent with how `KGClient`
    takes an injected neo4j `Driver` rather than owning its own connection —
    this lets tests pass an in-memory sqlite session without touching a
    real Postgres instance.
    """

    def __init__(self, session: Session):
        self.session = session

    def save_trace(self, trace: ReasoningTrace) -> None:
        """Persist a single ReasoningTrace, upserting by id."""
        record = self.session.get(TraceRecord, str(trace.id))
        if record is None:
            record = TraceRecord(id=str(trace.id))
            self.session.add(record)

        record.user_prompt = trace.user_prompt
        record.generated_code = trace.generated_code
        record.attempt_number = trace.attempt_number
        record.kg_context = trace.kg_context
        record.llm_reasoning = trace.llm_reasoning
        record.verification_status = (
            trace.verification_result.status.value if trace.verification_result else None
        )
        record.verification_result = (
            json.loads(trace.verification_result.model_dump_json())
            if trace.verification_result
            else None
        )
        record.failure_reason = trace.failure_reason
        record.confidence_score = trace.confidence_score
        record.created_at = trace.created_at

        self.session.commit()

    def save_traces(self, traces: list[ReasoningTrace]) -> None:
        """Persist the full attempt history of one request (order preserved)."""
        for trace in traces:
            self.save_trace(trace)

    def get_trace(self, trace_id: UUID) -> Optional[ReasoningTrace]:
        """Fetch a single trace by id, or None if not found."""
        record = self.session.get(TraceRecord, str(trace_id))
        return self._to_pydantic(record) if record is not None else None

    def get_traces_by_prompt(self, user_prompt: str) -> list[ReasoningTrace]:
        """Fetch all traces recorded for a given user prompt, in attempt order."""
        records = (
            self.session.query(TraceRecord)
            .filter(TraceRecord.user_prompt == user_prompt)
            .order_by(TraceRecord.created_at, TraceRecord.attempt_number)
            .all()
        )
        return [self._to_pydantic(r) for r in records]

    def _to_pydantic(self, record: TraceRecord) -> ReasoningTrace:
        verification_result = (
            VerificationResult(**record.verification_result)
            if record.verification_result is not None
            else None
        )
        return ReasoningTrace(
            id=UUID(record.id),
            user_prompt=record.user_prompt,
            generated_code=record.generated_code,
            attempt_number=record.attempt_number,
            kg_context=record.kg_context,
            llm_reasoning=record.llm_reasoning,
            verification_result=verification_result,
            failure_reason=record.failure_reason,
            confidence_score=record.confidence_score,
            created_at=record.created_at,
        )


def serialize_trace(trace: ReasoningTrace) -> str:
    """JSON serialization of a single trace, for API/CLI responses."""
    return trace.model_dump_json()


def serialize_traces(traces: list[ReasoningTrace]) -> str:
    """JSON serialization of a list of traces (e.g. GenerationResponse.traces)."""
    return json.dumps([json.loads(t.model_dump_json()) for t in traces])

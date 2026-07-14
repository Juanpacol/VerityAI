"""Audit log persistence (Phase 4 Part B): who did what, when, tied to a trace.

Mirrors agent/trace.py's TraceStore pattern: a SQLAlchemy ORM model + a
Store class taking an injected Session, so tests use an in-memory sqlite
session instead of a real Postgres instance.
"""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import JSON, Column, DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase, Session

from verityai.ontology.models import AuditLogEntry

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class AuditLogRecord(Base):
    """Relational row for one AuditLogEntry."""

    __tablename__ = "audit_log"

    id = Column(String(36), primary_key=True)
    actor = Column(String(255), nullable=False)
    action = Column(String(100), nullable=False)
    trace_id = Column(String(36), nullable=True)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False)


class AuditLogStore:
    """Records and retrieves audit log entries.

    Takes an injected SQLAlchemy `Session` (same pattern as `TraceStore`
    and `KGClient`'s injected driver), so tests never touch a real
    Postgres instance.
    """

    def __init__(self, session: Session):
        self.session = session

    def record(self, entry: AuditLogEntry) -> None:
        """Persist one audit log entry. Entries are append-only -- no update path."""
        record = AuditLogRecord(
            id=str(entry.id),
            actor=entry.actor,
            action=entry.action,
            trace_id=str(entry.trace_id) if entry.trace_id else None,
            details=entry.details,
            created_at=entry.created_at,
        )
        self.session.add(record)
        self.session.commit()
        logger.info(f"Audit log: actor={entry.actor} action={entry.action} trace_id={entry.trace_id}")

    def for_trace(self, trace_id: UUID) -> list[AuditLogEntry]:
        """All entries recorded against a given trace, oldest first."""
        records = (
            self.session.query(AuditLogRecord)
            .filter(AuditLogRecord.trace_id == str(trace_id))
            .order_by(AuditLogRecord.created_at)
            .all()
        )
        return [self._to_pydantic(r) for r in records]

    def all(self) -> list[AuditLogEntry]:
        """Every entry recorded so far, oldest first."""
        records = self.session.query(AuditLogRecord).order_by(AuditLogRecord.created_at).all()
        return [self._to_pydantic(r) for r in records]

    def _to_pydantic(self, record: AuditLogRecord) -> AuditLogEntry:
        return AuditLogEntry(
            id=UUID(record.id),
            actor=record.actor,
            action=record.action,
            trace_id=UUID(record.trace_id) if record.trace_id else None,
            details=record.details,
            created_at=record.created_at,
        )

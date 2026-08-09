"""Persistence for T5 study responses.

Mirrors agent/trace.py: one SQLAlchemy model against the shared `Base`, one
store class taking an injected `Session` so tests can hand it in-memory
sqlite instead of Postgres.
"""

import csv
import io
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import Boolean, DateTime, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from verityai.db.base import Base
from verityai.study.models import StudyResponse

logger = logging.getLogger(__name__)

# Column order for the CSV export. Fixed so a partially-analysed export
# doesn't shuffle between downloads.
CSV_COLUMNS = [
    "id",
    "run_id",
    "condition",
    "trusts_code",
    "merge_intent",
    "kept_element",
    "kept_element_other",
    "trust_reason",
    "reduced_trust_note",
    "comparison_to_current_tools",
    "experience_with_ai_tools",
    "created_at",
]


class StudyResponseRecord(Base):
    """Relational row for one participant's answers about one run."""

    __tablename__ = "study_responses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    condition: Mapped[str] = mapped_column(String(1))
    # Attitudinal and behavioural measures, stored separately on purpose --
    # see study/models.py.
    trusts_code: Mapped[bool] = mapped_column(Boolean)
    trust_reason: Mapped[str] = mapped_column(Text, default="")
    merge_intent: Mapped[str] = mapped_column(String(32))
    kept_element: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    kept_element_other: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reduced_trust_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    comparison_to_current_tools: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    experience_with_ai_tools: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class StudyResponseStore:
    """Persists and retrieves T5 study responses."""

    def __init__(self, session: Session):
        self.session = session

    def save(self, response: StudyResponse) -> None:
        """Persist one response, upserting by id."""
        record = self.session.get(StudyResponseRecord, str(response.id))
        if record is None:
            record = StudyResponseRecord(id=str(response.id))
            self.session.add(record)

        record.run_id = str(response.run_id)
        record.condition = response.condition
        record.trusts_code = response.trusts_code
        record.trust_reason = response.trust_reason
        record.merge_intent = response.merge_intent.value
        record.kept_element = response.kept_element.value if response.kept_element else None
        record.kept_element_other = response.kept_element_other
        record.reduced_trust_note = response.reduced_trust_note
        record.comparison_to_current_tools = response.comparison_to_current_tools
        record.experience_with_ai_tools = response.experience_with_ai_tools
        record.created_at = response.created_at

        self.session.commit()

    def get(self, response_id: UUID) -> Optional[StudyResponse]:
        record = self.session.get(StudyResponseRecord, str(response_id))
        return self._to_pydantic(record) if record is not None else None

    def list_all(self) -> list[StudyResponse]:
        """Every response, oldest first -- the analysis order."""
        records = (
            self.session.execute(
                select(StudyResponseRecord).order_by(StudyResponseRecord.created_at)
            )
            .scalars()
            .all()
        )
        return [self._to_pydantic(record) for record in records]

    @staticmethod
    def _to_pydantic(record: StudyResponseRecord) -> StudyResponse:
        return StudyResponse(
            id=UUID(record.id),
            run_id=UUID(record.run_id),
            condition=record.condition,
            trusts_code=record.trusts_code,
            trust_reason=record.trust_reason or "",
            merge_intent=record.merge_intent,
            kept_element=record.kept_element,
            kept_element_other=record.kept_element_other,
            reduced_trust_note=record.reduced_trust_note,
            comparison_to_current_tools=record.comparison_to_current_tools,
            experience_with_ai_tools=record.experience_with_ai_tools,
            created_at=record.created_at,
        )


def to_csv(responses: list[StudyResponse]) -> str:
    """Render responses as CSV for offline analysis."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for response in responses:
        row = response.model_dump(mode="json")
        writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})
    return buffer.getvalue()

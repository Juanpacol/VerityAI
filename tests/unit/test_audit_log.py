"""Unit tests for compliance/audit_log.py.

Uses an in-memory sqlite engine instead of Postgres -- AuditLogStore takes
an injected SQLAlchemy Session, same pattern as TraceStore.
"""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from verityai.compliance.audit_log import AuditLogStore, Base
from verityai.ontology.models import AuditLogEntry


@pytest.fixture
def store():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield AuditLogStore(session)
    session.close()


def make_entry(actor="alice", action="generate", trace_id=None, **overrides):
    defaults = dict(actor=actor, action=action, trace_id=trace_id or uuid4())
    defaults.update(overrides)
    return AuditLogEntry(**defaults)


class TestAuditLogStore:
    def test_record_then_for_trace_returns_it(self, store):
        entry = make_entry()
        store.record(entry)

        results = store.for_trace(entry.trace_id)

        assert len(results) == 1
        assert results[0].actor == "alice"
        assert results[0].action == "generate"

    def test_for_trace_excludes_other_traces(self, store):
        store.record(make_entry(trace_id=uuid4()))
        target_id = uuid4()
        store.record(make_entry(trace_id=target_id, actor="bob"))

        results = store.for_trace(target_id)

        assert len(results) == 1
        assert results[0].actor == "bob"

    def test_all_returns_every_entry_in_order(self, store):
        first = make_entry(actor="alice")
        second = make_entry(actor="bob")
        store.record(first)
        store.record(second)

        results = store.all()

        assert [e.actor for e in results] == ["alice", "bob"]

    def test_details_round_trip(self, store):
        entry = make_entry(details={"prompt": "write a divide function", "status": "success"})
        store.record(entry)

        result = store.for_trace(entry.trace_id)[0]

        assert result.details == {"prompt": "write a divide function", "status": "success"}

    def test_entry_without_trace_id_round_trips(self, store):
        entry = AuditLogEntry(actor="system", action="report_exported", trace_id=None)
        store.record(entry)

        result = store.all()[0]

        assert result.trace_id is None

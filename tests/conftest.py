"""Shared pytest fixtures for the whole test suite.

Centralizes patterns that were previously copy-pasted (with drift) across
multiple test files: an in-memory sqlite session (StaticPool, so FastAPI's
worker-thread execution doesn't see a fresh blank database -- see
api/rest.py's `_get_engine` for why), the rate-limit reset, and a fully
wired API TestClient.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from verityai.agent.trace import TraceStore
from verityai.api.rate_limit import reset_rate_limit_state
from verityai.api.rest import app, get_audit_log_store, get_trace_store
from verityai.compliance.audit_log import AuditLogStore
from verityai.db.base import Base


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Every API test shares one pseudo client IP through TestClient, so
    without this, request counts would accumulate across the whole test
    session and eventually trip 429s unrelated to what a given test is
    actually checking."""
    reset_rate_limit_state()
    yield


@pytest.fixture
def sqlite_engine() -> Engine:
    """A fresh in-memory sqlite engine with every ORM table created.

    StaticPool + check_same_thread=False: a plain sqlite in-memory DB is
    otherwise per-connection, and FastAPI runs sync endpoints in a worker
    thread -- without this, that thread would see a fresh, table-less
    database instead of the one this fixture just set up.
    """
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session(sqlite_engine: Engine) -> Session:
    session = sessionmaker(bind=sqlite_engine)()
    yield session
    session.close()


@pytest.fixture
def api_client(db_session: Session):
    """A TestClient wired to `db_session` for trace/audit-log storage.

    Orchestrator/KG dependencies are intentionally left un-overridden here
    -- tests that call /generate or /kg/* still need to override
    get_orchestrator / get_kg_client themselves with a FakeLLMClient or
    fake Neo4j driver, since what LLM/KG behavior to simulate is
    test-specific, not shared setup.
    """
    app.dependency_overrides[get_trace_store] = lambda: TraceStore(db_session)
    app.dependency_overrides[get_audit_log_store] = lambda: AuditLogStore(db_session)
    yield TestClient(app)
    app.dependency_overrides.clear()

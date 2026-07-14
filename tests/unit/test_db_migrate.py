"""Unit tests for db/migrate.py's additive-only schema migration.

Simulates a database created before request_id/generation_seconds/
confidence_factors existed on `reasoning_traces` by creating the table with
raw SQL in the old shape, then checking ensure_additive_columns() brings it
up to date without touching existing data.
"""

from sqlalchemy import create_engine, inspect, text

from verityai.db.migrate import ensure_additive_columns

OLD_SHAPE_DDL = """
CREATE TABLE reasoning_traces (
    id VARCHAR(36) PRIMARY KEY,
    user_prompt TEXT,
    generated_code TEXT,
    attempt_number INTEGER,
    kg_context JSON,
    llm_reasoning TEXT,
    verification_status VARCHAR(20),
    verification_result JSON,
    failure_reason TEXT,
    confidence_score FLOAT,
    created_at DATETIME
)
"""


def _columns(engine, table_name: str) -> set:
    return {col["name"] for col in inspect(engine).get_columns(table_name)}


class TestEnsureAdditiveColumns:
    def test_adds_missing_columns_to_old_shape_table(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(text(OLD_SHAPE_DDL))

        ensure_additive_columns(engine)

        columns = _columns(engine, "reasoning_traces")
        assert "request_id" in columns
        assert "generation_seconds" in columns
        assert "confidence_factors" in columns

    def test_rerun_is_idempotent(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(text(OLD_SHAPE_DDL))

        ensure_additive_columns(engine)
        ensure_additive_columns(engine)  # must not raise ("duplicate column name")

        assert "request_id" in _columns(engine, "reasoning_traces")

    def test_no_op_when_no_tables_exist_yet(self):
        engine = create_engine("sqlite:///:memory:")
        ensure_additive_columns(engine)  # must not raise

    def test_existing_data_survives_migration(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(text(OLD_SHAPE_DDL))
            conn.execute(
                text(
                    "INSERT INTO reasoning_traces "
                    "(id, user_prompt, generated_code, attempt_number, confidence_score) "
                    "VALUES ('abc', 'legacy prompt', 'x = 1', 1, 0.5)"
                )
            )

        ensure_additive_columns(engine)

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT user_prompt, request_id FROM reasoning_traces WHERE id = 'abc'")
            ).one()
        assert row[0] == "legacy prompt"
        assert row[1] is None

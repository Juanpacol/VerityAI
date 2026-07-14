"""Additive-only schema migration for SQLAlchemy models.

`Base.metadata.create_all(engine)` only creates tables that don't exist
yet -- it never alters an *existing* table's columns. Since new ontology
fields are always added as Optional/nullable (see `ReasoningTrace`'s
`request_id`/`generation_seconds`/`confidence_factors`), a database created
before those fields existed needs its table ALTER'd to gain the new
columns, or every read against it crashes with "no such column."

This is deliberately not Alembic: the only operation ever needed here is
"add a nullable column that isn't there yet," and a full migration
framework with revision history is more machinery than that one operation
justifies for a single-developer project at this stage. If a future change
needs anything other than an additive column (rename, drop, type change,
non-nullable backfill), that's the signal to introduce Alembic -- not to
extend this module to do it.
"""

import logging

from sqlalchemy import Engine, inspect, text

from verityai.db.base import Base

logger = logging.getLogger(__name__)


def ensure_additive_columns(engine: Engine) -> None:
    """Add any column present in the ORM models but missing from the live DB.

    Safe to call on every startup: a no-op once the schema already matches
    (re-running is idempotent). Only ever ADDs nullable columns to
    already-existing tables -- never renames, drops, or alters an existing
    column, since none of those are safely additive without data loss risk.
    Brand-new tables are left to `Base.metadata.create_all()`, which already
    handles that case.

    Args:
        engine: SQLAlchemy engine, already past `create_all()`
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue

            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue

                column_type = column.type.compile(dialect=engine.dialect)
                conn.execute(
                    text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}")
                )
                logger.info(f"Added column {table.name}.{column.name} ({column_type})")

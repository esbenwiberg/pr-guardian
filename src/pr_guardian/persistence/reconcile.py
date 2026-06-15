"""Idempotent column reconcile for existing databases.

``Base.metadata.create_all`` (run on startup) adds missing *tables* but never
adds missing *columns* to tables that already exist. After the migration
baseline squash (ADR-010), a database that had not run every pre-squash
migration can be stamped at the baseline while still missing a recent column —
the new model then SELECTs a column the table lacks and every query 500s.

This reconciler closes that gap: for each model column missing from an existing
table it issues ``ALTER TABLE ... ADD COLUMN``, but only when the column is safe
to add to a populated table (nullable, or has a server default). A NOT NULL
column without a server default cannot be back-filled here, so it is logged
loudly and left for a real migration rather than crashing startup.

It is a no-op on a correct schema and is meant to run from docker-entrypoint.sh
after ``alembic upgrade head``.
"""

from __future__ import annotations

import structlog
from sqlalchemy import inspect
from sqlalchemy.schema import CreateColumn

from pr_guardian.persistence.database import _get_engine
from pr_guardian.persistence.models import Base

log = structlog.get_logger()


async def reconcile_schema() -> dict[str, list[str]]:
    """Add model columns missing from existing tables. Returns {table: [added cols]}."""
    added: dict[str, list[str]] = {}
    engine = _get_engine()
    async with engine.begin() as conn:

        def _reconcile(sync_conn) -> None:
            inspector = inspect(sync_conn)
            for table in Base.metadata.sorted_tables:
                if not inspector.has_table(table.name):
                    # Missing tables are created by create_all / the baseline.
                    continue
                existing = {c["name"] for c in inspector.get_columns(table.name)}
                for column in table.columns:
                    if column.name in existing:
                        continue
                    safe_to_add = column.nullable or column.server_default is not None
                    if not safe_to_add:
                        log.error(
                            "schema_reconcile_unsafe_missing_column",
                            table=table.name,
                            column=column.name,
                            hint="NOT NULL without server default — needs a migration",
                        )
                        continue
                    column_ddl = CreateColumn(column).compile(dialect=sync_conn.dialect)
                    sync_conn.exec_driver_sql(
                        f'ALTER TABLE "{table.name}" ADD COLUMN {column_ddl}'
                    )
                    added.setdefault(table.name, []).append(column.name)
                    log.warning(
                        "schema_reconcile_added_column",
                        table=table.name,
                        column=column.name,
                    )

        await conn.run_sync(_reconcile)
    if not added:
        log.info("schema_reconcile_no_gaps")
    return added


def main() -> None:
    import asyncio

    asyncio.run(reconcile_schema())


if __name__ == "__main__":
    main()

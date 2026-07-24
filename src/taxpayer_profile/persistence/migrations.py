"""Ordered SQLite schema migrations tracked by ``PRAGMA user_version``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import Connection, inspect

SCHEMA_VERSION = 9


@dataclass(frozen=True)
class SchemaMigration:
    """One forward-only, idempotent SQLite schema change."""

    version: int
    description: str
    apply: Callable[[Connection], None]


def _add_column_if_missing(
    connection: Connection, *, table: str, column: str, sql_type: str
) -> None:
    inspector = inspect(connection)
    if table not in inspector.get_table_names():
        return
    actual = {item["name"] for item in inspector.get_columns(table)}
    if column not in actual:
        # Identifiers and types are fixed in the migration registry below;
        # this helper must never be called with user-controlled values.
        connection.exec_driver_sql(
            f'ALTER TABLE "{table}" ADD COLUMN "{column}" {sql_type}'
        )


def _migration_7(connection: Connection) -> None:
    _add_column_if_missing(
        connection,
        table="call_trajectories",
        column="secondary_topic",
        sql_type="TEXT",
    )


def _migration_8(connection: Connection) -> None:
    _add_column_if_missing(
        connection,
        table="call_trajectories",
        column="agent_answer_summary",
        sql_type="TEXT",
    )


def _migration_9(connection: Connection) -> None:
    # Imported lazily to keep the migration registry independent from model
    # import order during application startup.
    from taxpayer_profile.models import IngestionConflict

    IngestionConflict.__table__.create(connection, checkfirst=True)


MIGRATIONS = (
    SchemaMigration(7, "add call secondary topic", _migration_7),
    SchemaMigration(8, "add model-derived agent answer summary", _migration_8),
    SchemaMigration(9, "add rejected ingestion conflict audit", _migration_9),
)

if tuple(item.version for item in MIGRATIONS) != tuple(
    sorted({item.version for item in MIGRATIONS})
):
    raise RuntimeError("数据库迁移版本必须唯一且严格递增")
if MIGRATIONS and MIGRATIONS[-1].version != SCHEMA_VERSION:
    raise RuntimeError("最新迁移版本必须与 SCHEMA_VERSION 一致")


def sqlite_schema_version(connection: Connection) -> int:
    """Read the schema version stored in the SQLite database header."""

    return int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())


def apply_pending_sqlite_migrations(
    connection: Connection, *, current_version: int
) -> None:
    """Apply registered migrations newer than ``current_version``.

    Versions before the first registered migration predate the maintained
    migration history. The compatibility validator runs immediately afterward
    and rejects any legacy shape that cannot be upgraded safely.
    """

    if current_version > SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {current_version} 高于当前程序支持的版本 {SCHEMA_VERSION}，"
            "请升级程序后再打开该数据库。"
        )
    for migration in MIGRATIONS:
        if migration.version > current_version:
            migration.apply(connection)

"""Database engine, schema, and transactional session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from taxpayer_profile.models import Base
from taxpayer_profile.persistence.migrations import (
    SCHEMA_VERSION,
    apply_pending_sqlite_migrations,
    sqlite_schema_version,
)


def make_engine(database: Path | str) -> Engine:
    """Create a SQLite engine without creating tables implicitly."""

    if isinstance(database, Path) or "://" not in str(database):
        path = Path(database).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path}"
    else:
        url = str(database)
    engine = create_engine(url, future=True)

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _validate_existing_tables(connection: Connection) -> None:
    """Reject shapes that cannot be upgraded by registered safe migrations."""

    inspector = inspect(connection)
    incompatibilities: list[str] = []
    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        actual = {column["name"] for column in inspector.get_columns(table.name)}
        expected = {column.name for column in table.columns}
        missing = sorted(expected - actual)
        if missing:
            incompatibilities.append(f"{table.name} 缺少字段：{', '.join(missing)}")
    if incompatibilities:
        raise RuntimeError(
            "数据库结构与当前代码不兼容，且无法通过已注册迁移安全升级；"
            "请备份后删除旧数据库并重新构建。"
            + "；".join(incompatibilities)
        )


def create_schema(engine: Engine) -> None:
    """Migrate, validate and create the application schema atomically."""

    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            current_version = sqlite_schema_version(connection)
            apply_pending_sqlite_migrations(
                connection, current_version=current_version
            )
        _validate_existing_tables(connection)
        Base.metadata.create_all(connection)
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql(f"PRAGMA user_version={SCHEMA_VERSION}")


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def transactional_session(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit all database changes together or roll everything back."""

    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

from datetime import datetime
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from taxpayer_profile.database import create_schema, make_engine, make_session_factory
from taxpayer_profile.models import CallTrajectory


def test_database_creates_profile_and_auth_tables(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profiles.sqlite3")
    create_schema(engine)

    assert set(inspect(engine).get_table_names()) == {
        "caller_profiles",
        "call_trajectories",
        "update_logs",
        "system_users",
        "auth_sessions",
    }


def test_call_trajectory_stores_required_raw_fields_but_not_enterprise_private_fields(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path / "profiles.sqlite3")
    create_schema(engine)
    columns = {item["name"] for item in inspect(engine).get_columns("call_trajectories")}

    assert {
        "registration_time",
        "raw_call_start_time",
        "call_end_time",
        "raw_transcript",
        "agent_id",
        "agent_name",
        "business_content",
        "answer_content",
        "recording_path",
        "registration_unit",
        "handling_method",
        "business_category",
        "raw_phone_encrypted",
        "satisfaction",
        "call_serial_number",
        "topic_category",
        "secondary_topic",
        "demand_category",
        "unresolved_reason",
    }.issubset(columns)
    assert columns.isdisjoint({"taxpayer_name", "social_credit_code"})


def test_profile_does_not_persist_realtime_advice(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profiles.sqlite3")
    create_schema(engine)
    columns = {
        item["name"]
        for item in inspect(engine).get_columns("caller_profiles")
    }
    assert columns.isdisjoint(
        {"attention_level", "recommended_mode", "strategy_reason", "service_suggestion"}
    )


def test_business_id_has_a_unique_constraint(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profiles.sqlite3")
    create_schema(engine)
    sessions = make_session_factory(engine)
    common = {
        "phone_hash": "a" * 64,
        "call_time": datetime(2026, 6, 1, 9, 0),
        "analysis_status": "completed",
        "analysis_version": "test-v1",
    }

    with sessions() as session:
        session.add(CallTrajectory(business_id="BIZ-001", **common))
        session.commit()
        session.add(CallTrajectory(business_id="BIZ-001", **common))
        with pytest.raises(IntegrityError):
            session.commit()


def test_incompatible_database_requires_clean_rebuild(tmp_path: Path) -> None:
    database = tmp_path / "old.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE caller_profiles (phone_hash TEXT PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="删除旧数据库并重新构建"):
        create_schema(make_engine(database))

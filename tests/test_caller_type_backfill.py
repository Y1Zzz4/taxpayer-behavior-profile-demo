from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from taxpayer_profile.application.caller_type_backfill import (
    backfill_missing_caller_types,
)
from taxpayer_profile.database import (
    create_schema,
    make_engine,
    make_session_factory,
    transactional_session,
)
from taxpayer_profile.models import CallerProfile, CallTrajectory


def _seed(path: Path) -> None:
    engine = make_engine(path)
    create_schema(engine)
    sessions = make_session_factory(engine)
    call_time = datetime(2026, 7, 24, 9)
    with transactional_session(sessions) as session:
        session.add_all(
            [
                CallerProfile(
                    phone_hash="a" * 64,
                    phone_encrypted="encrypted",
                    caller_type="无法判断",
                    enterprise_identity="无法判断",
                    first_call_time=call_time,
                    latest_call_time=call_time,
                    latest_business_id="UNKNOWN-1",
                ),
                CallTrajectory(
                    business_id="UNKNOWN-1",
                    phone_hash="a" * 64,
                    call_time=call_time,
                    caller_type=None,
                    enterprise_identity="无法判断",
                    analysis_status="completed",
                    analysis_version="test",
                ),
                CallTrajectory(
                    business_id="KNOWN-1",
                    phone_hash="b" * 64,
                    call_time=call_time,
                    caller_type="企业",
                    enterprise_identity="办税人员",
                    analysis_status="completed",
                    analysis_version="test",
                ),
            ]
        )


def test_caller_type_backfill_is_dry_run_then_repairs_only_missing_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "profiles.sqlite3"
    _seed(database)
    source_rows = [
        {"业务编号": "UNKNOWN-1", "咨询主体(大模型判断)": "个人"},
        {"业务编号": "KNOWN-1", "咨询主体(大模型判断)": "个人"},
    ]

    preview = backfill_missing_caller_types(
        database_path=database, source_rows=source_rows
    )
    assert preview.dry_run is True
    assert (preview.eligible_call_count, preview.updated_profile_count) == (1, 1)

    sessions = make_session_factory(make_engine(database))
    with sessions() as session:
        assert session.get(CallTrajectory, "UNKNOWN-1").caller_type is None  # type: ignore[union-attr]

    applied = backfill_missing_caller_types(
        database_path=database, source_rows=source_rows, dry_run=False
    )
    assert applied.dry_run is False
    assert applied.updated_call_count == 1
    with sessions() as session:
        repaired = session.get(CallTrajectory, "UNKNOWN-1")
        unchanged = session.get(CallTrajectory, "KNOWN-1")
        profile = session.scalar(select(CallerProfile).where(CallerProfile.phone_hash == "a" * 64))
        assert repaired is not None and unchanged is not None and profile is not None
        assert (repaired.caller_type, repaired.enterprise_identity) == ("个人", "不适用")
        assert unchanged.caller_type == "企业"
        assert (profile.caller_type, profile.enterprise_identity) == ("个人", "不适用")


def test_caller_type_backfill_rejects_conflicting_source_rows(tmp_path: Path) -> None:
    database = tmp_path / "profiles.sqlite3"
    _seed(database)
    with pytest.raises(ValueError, match="咨询主体冲突"):
        backfill_missing_caller_types(
            database_path=database,
            source_rows=[
                {"业务编号": "UNKNOWN-1", "咨询主体(大模型判断)": "个人"},
                {"业务编号": "UNKNOWN-1", "咨询主体(大模型判断)": "企业"},
            ],
        )

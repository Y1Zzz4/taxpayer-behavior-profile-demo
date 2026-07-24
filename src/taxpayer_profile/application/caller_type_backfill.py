"""Controlled repair for legacy trajectories missing a caller type."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from sqlalchemy import or_, select

from taxpayer_profile.database import make_engine, make_session_factory, transactional_session
from taxpayer_profile.identity import normalize_caller_type
from taxpayer_profile.models import CallerProfile, CallTrajectory
from taxpayer_profile.normalization import clean_identifier
from taxpayer_profile.profiles.aggregation import update_profile

CALLER_TYPE_SOURCE_COLUMN = "咨询主体(大模型判断)"


@dataclass(frozen=True)
class CallerTypeBackfillSummary:
    """Counts from one explicit, source-controlled caller-type repair."""

    source_type_count: int
    eligible_call_count: int
    updated_call_count: int
    updated_profile_count: int
    dry_run: bool


def _source_types_by_business_id(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    """Extract unambiguous binary caller types from one supplied source."""

    source_types: dict[str, str] = {}
    for position, row in enumerate(rows, start=1):
        business_id = clean_identifier(row.get("业务编号"))
        caller_type = normalize_caller_type(row.get(CALLER_TYPE_SOURCE_COLUMN))
        if business_id is None or caller_type is None:
            continue
        previous = source_types.setdefault(business_id, caller_type)
        if previous != caller_type:
            raise ValueError(
                f"第 {position} 条记录的业务编号与同一来源中的咨询主体冲突：{business_id}"
            )
    return source_types


def backfill_missing_caller_types(
    *,
    database_path: Path | str,
    source_rows: Sequence[Mapping[str, object]],
    dry_run: bool = True,
) -> CallerTypeBackfillSummary:
    """Fill only missing legacy caller types from an explicitly supplied column.

    This repair never infers a type from identity labels, never overwrites an
    existing binary result, and never becomes part of ordinary incremental
    ingestion. Profiles affected by a repaired trajectory are rebuilt in the
    same transaction so their latest-call projection remains consistent.
    """

    if not any(CALLER_TYPE_SOURCE_COLUMN in row for row in source_rows):
        raise ValueError(f"回填来源缺少字段：{CALLER_TYPE_SOURCE_COLUMN}")
    source_types = _source_types_by_business_id(source_rows)
    engine = make_engine(database_path)
    sessions = make_session_factory(engine)

    with sessions() as session:
        candidates = list(
            session.scalars(
                select(CallTrajectory).where(
                    or_(
                        CallTrajectory.caller_type.is_(None),
                        CallTrajectory.caller_type == "无法判断",
                    )
                )
            )
        )
    repaired = [item for item in candidates if item.business_id in source_types]
    affected_hashes = {item.phone_hash for item in repaired}
    summary = CallerTypeBackfillSummary(
        source_type_count=len(source_types),
        eligible_call_count=len(repaired),
        updated_call_count=len(repaired),
        updated_profile_count=len(affected_hashes),
        dry_run=dry_run,
    )
    if dry_run or not repaired:
        return summary

    with transactional_session(sessions) as session:
        for trajectory in repaired:
            caller_type = source_types[trajectory.business_id]
            trajectory.caller_type = caller_type
            if caller_type == "个人":
                trajectory.enterprise_identity = "不适用"
            elif not trajectory.enterprise_identity:
                trajectory.enterprise_identity = "无法判断"
            session.add(trajectory)

        for phone_hash in affected_hashes:
            profile = session.get(CallerProfile, phone_hash)
            if profile is None:
                continue
            history = list(
                session.scalars(
                    select(CallTrajectory).where(CallTrajectory.phone_hash == phone_hash)
                )
            )
            update_profile(profile=profile, trajectories=history)
    return summary

"""Read-only aggregation for the dashboard use case."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from taxpayer_profile.application.web_dto import (
    counter_rows,
    district_unit_label,
    resolution_rows,
    resolution_state,
    secondary_labels_for_topic,
    segmented_rows,
    split_labels,
)
from taxpayer_profile.application.web_profile_support import (
    HISTORICAL_FACT_DEFINITIONS,
    fact_counts,
)
from taxpayer_profile.models import CallerProfile, CallTrajectory, UpdateLog
from taxpayer_profile.service_calendar import recent_service_days


class DashboardService:
    """Assemble dashboard projections without mutating operational data."""

    def __init__(self, sessions: Callable[[], Session]) -> None:
        self._sessions = sessions

    def summary(self) -> dict[str, object]:
        with self._sessions() as session:
            profiles = session.scalars(select(CallerProfile)).all()
            trajectories = session.scalars(select(CallTrajectory)).all()
            latest_update = session.scalar(
                select(UpdateLog).order_by(UpdateLog.started_at.desc()).limit(1)
            )

        known_resolution = [
            item.resolved_status
            for item in trajectories
            if item.resolved_status is not None
        ]
        daily_calls = Counter(item.call_time.date().isoformat() for item in trajectories)
        invalid_caller_types = [
            item.business_id
            for item in trajectories
            if item.caller_type not in {"企业", "个人"}
        ]
        if invalid_caller_types:
            # Do not silently normalize a broken invariant into a dashboard
            # category. Legacy data must be repaired before it is presented.
            raise RuntimeError("数据库存在未完成咨询主体修复的来电记录")

        caller_types = Counter(item.caller_type for item in trajectories)
        topics: Counter[str] = Counter()
        demands: Counter[str] = Counter()
        topic_resolution: dict[str, Counter[str]] = {}
        secondary_topics: dict[str, Counter[str]] = {}
        secondary_resolution: dict[str, dict[str, Counter[str]]] = {}
        demand_resolution: dict[str, Counter[str]] = {}
        registration_units: dict[str, Counter[str]] = {}
        for item in trajectories:
            state = resolution_state(item)
            primary_labels = split_labels(item.topic_category, fallback="暂未分类")
            secondaries = split_labels(item.secondary_topic, fallback="二级专题待识别")
            for topic in primary_labels:
                topics[topic] += 1
                topic_resolution.setdefault(topic, Counter())[state] += 1
                for secondary in secondary_labels_for_topic(
                    topic, primary_labels, secondaries
                ):
                    secondary_topics.setdefault(topic, Counter())[secondary] += 1
                    secondary_resolution.setdefault(topic, {}).setdefault(
                        secondary, Counter()
                    )[state] += 1
            for label in split_labels(item.demand_category, fallback="暂未分类"):
                demands[label] += 1
                demand_resolution.setdefault(label, Counter())[state] += 1
            unit = district_unit_label(item.registration_unit)
            registration_units.setdefault(unit, Counter())["total"] += 1
            registration_units[unit][state] += 1

        sorted_dates = sorted(daily_calls)
        trend_dates = (
            recent_service_days(
                max(item.call_time.date() for item in trajectories), count=14
            )
            if sorted_dates
            else []
        )
        facts = fact_counts(trajectories)
        fact_rows = [
            {
                **definition,
                "value": facts[str(definition["id"])],
                "share": round(
                    facts[str(definition["id"])] * 100 / len(trajectories), 1
                )
                if trajectories
                else 0,
            }
            for definition in HISTORICAL_FACT_DEFINITIONS
        ]
        return {
            "overview": {
                "total_profiles": len(profiles),
                "total_calls": len(trajectories),
                "work_orders": facts["work_order"],
                "resolved_rate": (
                    round(
                        100
                        * sum(value is True for value in known_resolution)
                        / len(known_resolution),
                        1,
                    )
                    if known_resolution
                    else None
                ),
                "data_date_range": (
                    f"{sorted_dates[0]} 至 {sorted_dates[-1]}" if sorted_dates else None
                ),
            },
            "daily_calls": [
                {
                    "date": value.isoformat(),
                    "label": value.strftime("%m-%d"),
                    "value": daily_calls[value.isoformat()],
                }
                for value in trend_dates
            ],
            "caller_types": counter_rows(caller_types),
            "resolution_status": resolution_rows(trajectories),
            "personal_resolution": resolution_rows(
                [item for item in trajectories if item.caller_type == "个人"]
            ),
            "enterprise_resolution": resolution_rows(
                [item for item in trajectories if item.caller_type == "企业"]
            ),
            "question_categories": [
                {
                    **row,
                    "children": segmented_rows(
                        secondary_topics.get(str(row["label"]), Counter()),
                        secondary_resolution.get(str(row["label"]), {}),
                        limit=5,
                    ),
                }
                for row in segmented_rows(
                    topics, topic_resolution, limit=5, exclude_other=True
                )
            ],
            "demand_categories": segmented_rows(
                demands, demand_resolution, limit=5, exclude_other=True
            ),
            "registration_unit_resolution": [
                {
                    "label": label,
                    "total": counts["total"],
                    "resolved": counts["resolved"],
                    "unresolved": counts["unresolved"],
                    "unknown": counts["unknown"],
                    "resolved_rate": (
                        round(
                            counts["resolved"]
                            * 100
                            / (counts["resolved"] + counts["unresolved"]),
                            1,
                        )
                        if counts["resolved"] + counts["unresolved"]
                        else None
                    ),
                }
                for label, counts in sorted(
                    registration_units.items(), key=lambda item: (-item[1]["total"], item[0])
                )
            ],
            "historical_facts": fact_rows,
            # Kept while the browser consumes the established dashboard contract.
            "service_signals": [
                {"label": row["label"], "value": row["value"]} for row in fact_rows
            ],
            "latest_update": (
                {
                    "data_date": latest_update.data_date,
                    "input_filename": latest_update.input_filename,
                    "status": latest_update.status,
                    "finished_at": latest_update.finished_at,
                    "new_call_count": latest_update.new_call_count,
                    "new_phone_count": latest_update.new_phone_count,
                    "failed_count": latest_update.failed_count,
                    "summary": latest_update.summary,
                }
                if latest_update is not None
                else None
            ),
        }

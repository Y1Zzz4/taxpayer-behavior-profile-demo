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
    split_labels,
    unresolved_rate_rows,
)
from taxpayer_profile.application.web_profile_support import (
    DASHBOARD_HISTORICAL_FACT_DEFINITIONS,
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
        enterprise_identity_items: dict[str, list[CallTrajectory]] = {}
        unresolved_question_hotspots: dict[str, Counter[str]] = {
            "all": Counter(),
            "personal": Counter(),
            "enterprise": Counter(),
        }
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
            identity = (item.enterprise_identity or "").strip()
            if item.caller_type == "企业" and identity not in {"", "无法判断", "不适用"}:
                enterprise_identity_items.setdefault(identity, []).append(item)
            if item.resolved_status is False:
                question = self._hotspot_question_label(item.father_question_2)
                if question is not None:
                    unresolved_question_hotspots["all"][question] += 1
                    if item.caller_type == "个人":
                        unresolved_question_hotspots["personal"][question] += 1
                    elif item.caller_type == "企业":
                        unresolved_question_hotspots["enterprise"][question] += 1

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
            for definition in DASHBOARD_HISTORICAL_FACT_DEFINITIONS
        ]
        enterprise_identity_rates = [
            self._resolution_rate_from_items(label, items)
            for label, items in enterprise_identity_items.items()
        ]
        enterprise_identity_rates.sort(
            key=lambda item: (
                item["resolved_rate"] is None,
                -float(item["resolved_rate"] or 0),
                -int(item["eligible_total"]),
                str(item["label"]),
            )
        )
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
            "caller_resolution_rates": [
                self._resolution_rate_row("个人", trajectories),
                self._resolution_rate_row("企业", trajectories),
            ],
            "enterprise_identity_resolution_rates": enterprise_identity_rates,
            "question_categories": [
                {
                    **row,
                    "children": unresolved_rate_rows(
                        secondary_topics.get(str(row["label"]), Counter()),
                        secondary_resolution.get(str(row["label"]), {}),
                        limit=5,
                        exclude_other=True,
                        exclude_unclassified=True,
                    ),
                }
                for row in unresolved_rate_rows(
                    topics,
                    topic_resolution,
                    limit=5,
                    exclude_other=True,
                    exclude_unclassified=True,
                )
            ],
            "demand_categories": unresolved_rate_rows(
                demands, demand_resolution, limit=5, exclude_other=True
            ),
            "registration_unit_resolution": [
                {
                    "label": label,
                    "total": counts["total"],
                    "resolved": counts["resolved"],
                    "unresolved": counts["unresolved"],
                    "unknown": counts["unknown"],
                    "eligible_total": counts["resolved"] + counts["unresolved"],
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
                    registration_units.items(),
                    key=lambda item: (
                        -(
                            item[1]["resolved"] * 100
                            / (item[1]["resolved"] + item[1]["unresolved"])
                            if item[1]["resolved"] + item[1]["unresolved"]
                            else -1
                        ),
                        -(item[1]["resolved"] + item[1]["unresolved"]),
                        item[0],
                    ),
                )
            ],
            "historical_facts": fact_rows,
            "unresolved_question_hotspots": {
                group: counter_rows(counter, limit=5)
                for group, counter in unresolved_question_hotspots.items()
            },
            "unresolved_distributions": {
                "topics": [
                    {
                        **row,
                        "children": unresolved_rate_rows(
                        secondary_topics.get(str(row["label"]), Counter()),
                        secondary_resolution.get(str(row["label"]), {}),
                        limit=5,
                        exclude_other=True,
                        exclude_unclassified=True,
                        ),
                    }
                    for row in unresolved_rate_rows(
                        topics,
                        topic_resolution,
                        limit=5,
                        exclude_other=True,
                        exclude_unclassified=True,
                    )
                ],
                "demands": unresolved_rate_rows(
                    demands, demand_resolution, limit=5, exclude_other=True
                ),
            },
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

    @staticmethod
    def _hotspot_question_label(value: str | None) -> str | None:
        """Return only a usable parent question for the hotspot Top5."""

        label = (value or "").strip()
        normalized = label.strip("[](){}'\" ").lower()
        missing_values = {
            "",
            "nan",
            "none",
            "null",
            "n/a",
            "na",
            "未知",
            "未提取",
            "问题待归类",
            "未提取出标签",
        }
        if normalized in missing_values:
            return None
        lowered = label.lower()
        if (
            "均为“nan”" in label
            or "均为\"nan\"" in label
            or ("nan" in lowered and ("问题组" in label or "无法提炼" in label))
            or "无法提炼出核心问题" in label
            or "我无法给到相关内容" in label
            or "未提取出标签" in label
        ):
            return None
        return label

    @staticmethod
    def _resolution_rate_row(
        label: str, trajectories: list[CallTrajectory]
    ) -> dict[str, object]:
        return DashboardService._resolution_rate_from_items(
            label, [item for item in trajectories if item.caller_type == label]
        )

    @staticmethod
    def _resolution_rate_from_items(
        label: str, items: list[CallTrajectory]
    ) -> dict[str, object]:
        resolved = sum(item.resolved_status is True for item in items)
        unresolved = sum(item.resolved_status is False for item in items)
        unknown = len(items) - resolved - unresolved
        eligible_total = resolved + unresolved
        return {
            "label": label,
            "resolved": resolved,
            "unresolved": unresolved,
            "unknown": unknown,
            "eligible_total": eligible_total,
            "resolved_rate": (
                round(resolved * 100 / eligible_total, 1) if eligible_total else None
            ),
        }

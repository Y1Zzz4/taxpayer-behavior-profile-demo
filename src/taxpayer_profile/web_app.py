"""Small localhost-only HTTP application for the simulated inbound-call demo."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from functools import cached_property
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import ceil
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from sqlalchemy import func, select

from taxpayer_profile.config import PROJECT_ROOT, Settings
from taxpayer_profile.database import make_engine, make_session_factory
from taxpayer_profile.llm_client import OpenAICompatibleClient
from taxpayer_profile.models import CallerProfile, CallTrajectory, UpdateLog
from taxpayer_profile.profiling import build_service_strategy, classify_service_profile
from taxpayer_profile.query import query_profile
from taxpayer_profile.realtime_advice import (
    AdviceClient,
    build_fallback_advice,
    generate_realtime_advice,
)
from taxpayer_profile.security import PhoneProtector

WEB_ROOT = PROJECT_ROOT / "web"
MAX_REQUEST_BYTES = 16_384
SHOWCASE_SCENARIOS = (
    {
        "id": "baseline",
        "label": "当前画像",
        "description": "只展示现有历史证据，不增加模拟记录。",
    },
    {
        "id": "repeat_unresolved",
        "label": "同类事项再次未解决",
        "description": "模拟同一问题再次来电，且本通仍未直接解决。",
    },
    {
        "id": "resolved_closure",
        "label": "历史事项完成闭环",
        "description": "模拟来电核验进度后，将一项历史待办确认解决。",
    },
    {
        "id": "service_dissatisfaction",
        "label": "新增服务不满信号",
        "description": "模拟来电人对等待或转交过程表达不满。",
    },
)


def _showcase_key(phone_hash: str) -> str:
    return hashlib.sha256(f"profile-showcase:{phone_hash}".encode()).hexdigest()[:18]


def _strategy_payload(
    *,
    profile: CallerProfile,
    trajectories: list[CallTrajectory],
    state: dict[str, int],
    latest_resolved: bool | None,
) -> dict[str, object]:
    question = profile.latest_question or "最近咨询事项"
    unresolved_questions = [
        item.core_question or item.topic_category or "历史未解决事项"
        for item in trajectories
        if item.resolved_status is False
    ]
    classification = classify_service_profile(
        total_calls=state["total_calls"],
        repeated_issues=state["repeated_issues"],
        unresolved=state["unresolved"],
        work_orders=state["work_orders"],
        dissatisfaction=state["dissatisfaction"],
        proficiency_score=profile.proficiency_score,
    )
    strategy = build_service_strategy(
        total_calls=state["total_calls"],
        repeated_issues=state["repeated_issues"],
        unresolved=state["unresolved"],
        work_orders=state["work_orders"],
        abnormal_ends=state["abnormal_ends"],
        dissatisfaction=state["dissatisfaction"],
        has_pushback=any(item.potential_pushback is True for item in trajectories),
        latest_resolved=latest_resolved,
        proficiency_score=profile.proficiency_score,
        latest_question=question,
        recent_questions=[
            item.core_question
            for item in reversed(trajectories)
            if item.core_question
        ][:3],
        unresolved_questions=unresolved_questions[:3],
    )
    return {
        "profile_type": classification.profile_type,
        "profile_basis": classification.basis,
        "attention_level": strategy.attention_level,
        "service_mode": strategy.recommended_mode,
        "strategy_reason": strategy.reason,
        "service_suggestion": strategy.suggestion,
    }


@dataclass
class DemoService:
    database_path: Path
    protector: PhoneProtector
    settings: Settings
    advice_client_factory: Callable[[], AdviceClient | None] | None = None

    @cached_property
    def _sessions(self):  # type: ignore[no-untyped-def]
        return make_session_factory(make_engine(self.database_path))

    def lookup_profile(self, phone: object) -> dict[str, object] | None:
        return query_profile(
            phone=phone,
            database_path=self.database_path,
            protector=self.protector,
        )

    def _advice_client(self) -> AdviceClient | None:
        if self.advice_client_factory is not None:
            return self.advice_client_factory()
        if not self.settings.llm_configured:
            return None
        return OpenAICompatibleClient(
            self.settings.llm_base_url,  # type: ignore[arg-type]
            self.settings.llm_api_key,  # type: ignore[arg-type]
            self.settings.llm_model,  # type: ignore[arg-type]
            timeout_seconds=12.0,
            max_attempts=1,
        )

    def generate_advice(self, phone: object) -> dict[str, object]:
        profile = self.lookup_profile(phone)
        if profile is None:
            empty_context = {
                "profile_summary": "该号码暂无历史来电记录。",
                "proficiency_score": None,
                "proficiency_summary": "无法判断",
                "statistics": {},
                "recent_trajectories": [],
            }
            return {
                "found": False,
                "advice": build_fallback_advice(
                    empty_context, fallback_reason="profile_not_found"
                ),
            }
        return {
            "found": True,
            "advice": generate_realtime_advice(
                profile["agent_context"],  # type: ignore[arg-type]
                self._advice_client(),
            ),
        }

    def dashboard_summary(self) -> dict[str, object]:
        """Return aggregate, non-identifying statistics for the overview page."""

        with self._sessions() as session:
            profiles = session.scalars(select(CallerProfile)).all()
            trajectories = session.scalars(select(CallTrajectory)).all()
            latest_update = session.scalar(
                select(UpdateLog).order_by(UpdateLog.started_at.desc()).limit(1)
            )

        proficiency = [
            item.proficiency_score
            for item in profiles
            if item.proficiency_score is not None
        ]
        known_resolution = [
            item.resolved_status
            for item in trajectories
            if item.resolved_status is not None
        ]
        daily_calls = Counter(item.call_time.date().isoformat() for item in trajectories)
        caller_types = Counter(item.caller_type or "暂未识别" for item in profiles)
        service_profile_types = Counter(
            item.service_profile_type or "暂未分类" for item in profiles
        )
        service_ratings = Counter(
            item.service_rating or "暂未评价" for item in trajectories
        )
        question_categories: Counter[str] = Counter()
        demand_categories: Counter[str] = Counter()
        question_resolution: dict[str, Counter[str]] = {}
        demand_resolution: dict[str, Counter[str]] = {}
        for item in trajectories:
            resolution_key = (
                "resolved"
                if item.resolved_status is True
                else "unresolved"
                if item.resolved_status is False
                else "unknown"
            )
            topic_label = item.topic_category or "暂未分类"
            question_categories[topic_label] += 1
            question_resolution.setdefault(topic_label, Counter())[resolution_key] += 1
            labels = (
                [part.strip() for part in item.demand_category.split(",")]
                if item.demand_category
                else ["暂未分类"]
            )
            for label in labels:
                if not label:
                    continue
                demand_categories[label] += 1
                demand_resolution.setdefault(label, Counter())[resolution_key] += 1
        resolution_status = Counter(
            "已直接解决"
            if item.resolved_status is True
            else "未直接解决"
            if item.resolved_status is False
            else "状态待判定"
            for item in trajectories
        )
        proficiency_bands = Counter(
            "较熟练（8-10分）"
            if item.proficiency_score is not None and item.proficiency_score >= 8
            else "一般（5-7.9分）"
            if item.proficiency_score is not None and item.proficiency_score >= 5
            else "需更多引导（0-4.9分）"
            if item.proficiency_score is not None
            else "暂未评估"
            for item in profiles
        )
        if daily_calls:
            latest_date = max(item.call_time.date() for item in trajectories)
            first_date = min(item.call_time.date() for item in trajectories)
            trend_start = latest_date - timedelta(days=13)
            trend_dates = [
                trend_start + timedelta(days=offset)
                for offset in range((latest_date - trend_start).days + 1)
            ]
        else:
            first_date = latest_date = None
            trend_dates = []

        return {
            "overview": {
                "total_profiles": len(profiles),
                "total_calls": len(trajectories),
                "unresolved_calls": sum(
                    item.resolved_status is False for item in trajectories
                ),
                "work_orders": sum(item.work_order is True for item in trajectories),
                "repeated_calls": sum(item.is_repeated_call for item in trajectories),
                "repeated_issues": sum(
                    item.is_repeated_issue is True for item in trajectories
                ),
                "profiles_with_proficiency": len(proficiency),
                "average_proficiency": (
                    round(sum(proficiency) / len(proficiency), 1)
                    if proficiency
                    else None
                ),
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
                "average_calls_per_profile": (
                    round(len(trajectories) / len(profiles), 1) if profiles else 0
                ),
                "question_category_count": len(question_categories),
                "data_date_range": (
                    f"{first_date.isoformat()} 至 {latest_date.isoformat()}"
                    if first_date is not None and latest_date is not None
                    else None
                ),
            },
            "daily_calls": [
                {
                    "date": date.isoformat(),
                    "label": date.strftime("%m-%d"),
                    "value": daily_calls.get(date.isoformat(), 0),
                }
                for date in trend_dates
            ],
            "caller_types": _counter_rows(caller_types),
            "service_profile_types": _counter_rows(service_profile_types),
            "resolution_status": _counter_rows(resolution_status),
            "service_ratings": _counter_rows(service_ratings),
            "question_categories": _segmented_counter_rows(
                question_categories, question_resolution, limit=8
            ),
            "demand_categories": _segmented_counter_rows(
                demand_categories, demand_resolution
            ),
            "proficiency_bands": _counter_rows(proficiency_bands),
            "service_signals": [
                {
                    "label": "未直接解决",
                    "value": sum(
                        item.resolved_status is False for item in trajectories
                    ),
                },
                {
                    "label": "已形成工单",
                    "value": sum(item.work_order is True for item in trajectories),
                },
                {
                    "label": "重复来电",
                    "value": sum(item.is_repeated_call for item in trajectories),
                },
                {
                    "label": "重复事项",
                    "value": sum(
                        item.is_repeated_issue is True for item in trajectories
                    ),
                },
                {
                    "label": "服务需关注",
                    "value": sum(
                        item.taxpayer_dissatisfied is True
                        or item.service_rating == "需关注"
                        for item in trajectories
                    ),
                },
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

    def profile_showcase_catalog(self) -> dict[str, object]:
        """List masked example profiles for the read-only profile showcase."""

        with self._sessions() as session:
            profiles = session.scalars(
                select(CallerProfile).order_by(CallerProfile.latest_call_time.desc())
            ).all()

        items: list[dict[str, object]] = []
        for profile in profiles:
            try:
                masked_phone = _mask_phone(
                    self.protector.decrypt_phone(profile.phone_encrypted)
                )
            except (ValueError, TypeError):
                masked_phone = "号码信息不可用"
            items.append(
                {
                    "profile_key": _showcase_key(profile.phone_hash),
                    "masked_phone": masked_phone,
                    "profile_type": profile.service_profile_type or "暂未分类",
                    "latest_question": profile.latest_question or "最近咨询事项未记录",
                    "total_calls": profile.total_call_count,
                    "repeated_issues": profile.repeated_issue_count,
                    "unresolved": profile.unresolved_count,
                    "dissatisfaction": profile.dissatisfaction_count,
                    "latest_call_time": profile.latest_call_time,
                    "presentation_priority": (
                        100
                        if not any(
                            (
                                profile.dissatisfaction_count,
                                profile.unresolved_count,
                                profile.repeated_issue_count,
                            )
                        )
                        else 60
                        if profile.unresolved_count and not profile.dissatisfaction_count
                        else 30
                    )
                    + int(bool(profile.latest_question)),
                }
            )
        items.sort(
            key=lambda item: (
                -int(item["presentation_priority"]),
                str(item["latest_call_time"]),
            )
        )
        for item in items:
            item.pop("presentation_priority", None)
        return {
            "items": items,
            "scenarios": list(SHOWCASE_SCENARIOS),
            "methodology": [
                "先区分重复来电和同一问题重复咨询，避免仅凭号码频次下结论。",
                "核心问题保持具体到业务场景和实际诉求，再结合历史语义关系判断。",
                "画像按新增来电逐次更新，每个结论均保留到单通来电的证据入口。",
                "服务方式由画像事实映射生成，推演结果不写入数据库。",
            ],
        }

    def profile_showcase(
        self, *, profile_key: object, scenario: object = "baseline"
    ) -> dict[str, object]:
        """Return evidence, profile and a non-persistent incremental simulation."""

        key = str(profile_key or "").strip()
        scenario_id = str(scenario or "baseline").strip()
        scenario_map = {item["id"]: item for item in SHOWCASE_SCENARIOS}
        if not key:
            raise ValueError("缺少画像标识")
        if scenario_id not in scenario_map:
            raise ValueError("不支持该推演场景")

        with self._sessions() as session:
            profiles = session.scalars(select(CallerProfile)).all()
            profile = next(
                (item for item in profiles if _showcase_key(item.phone_hash) == key),
                None,
            )
            if profile is None:
                raise ValueError("未找到对应画像")
            trajectories = list(
                session.scalars(
                    select(CallTrajectory)
                    .where(CallTrajectory.phone_hash == profile.phone_hash)
                    .order_by(
                        CallTrajectory.call_time.asc(),
                        CallTrajectory.business_id.asc(),
                    )
                )
            )

        try:
            masked_phone = _mask_phone(
                self.protector.decrypt_phone(profile.phone_encrypted)
            )
        except (ValueError, TypeError):
            masked_phone = "号码信息不可用"

        state = {
            "total_calls": profile.total_call_count,
            "repeated_calls": profile.repeated_call_count,
            "repeated_issues": profile.repeated_issue_count,
            "unresolved": profile.unresolved_count,
            "work_orders": profile.work_order_count,
            "abnormal_ends": profile.abnormal_end_count,
            "dissatisfaction": profile.dissatisfaction_count,
        }
        before = _strategy_payload(
            profile=profile,
            trajectories=trajectories,
            state=state,
            latest_resolved=profile.latest_resolved,
        )
        after_state = state.copy()
        after_resolved = profile.latest_resolved
        scenario_event = "当前仅回放数据库中的历史事实。"
        if scenario_id == "repeat_unresolved":
            after_state["total_calls"] += 1
            after_state["repeated_calls"] += 1
            after_state["repeated_issues"] += 1
            after_state["unresolved"] += 1
            after_resolved = False
            scenario_event = "新增一通同类事项来电，经核对仍未直接解决。"
        elif scenario_id == "resolved_closure":
            after_state["total_calls"] += 1
            after_state["repeated_calls"] += 1
            after_state["repeated_issues"] += int(bool(trajectories))
            after_state["unresolved"] = max(0, after_state["unresolved"] - 1)
            after_resolved = True
            scenario_event = "新增一通进度核验来电，并确认一项历史待办已完成闭环。"
        elif scenario_id == "service_dissatisfaction":
            after_state["total_calls"] += 1
            after_state["repeated_calls"] += 1
            after_state["dissatisfaction"] += 1
            after_resolved = None
            scenario_event = "新增一通来电，来电人对等待或转交过程表达不满。"
        after = _strategy_payload(
            profile=profile,
            trajectories=trajectories,
            state=after_state,
            latest_resolved=after_resolved,
        )

        daily_counts = Counter(item.call_time.date() for item in trajectories)
        max_single_day = max(daily_counts.values(), default=0)
        rolling_three_day = 0
        if daily_counts:
            first_day = min(daily_counts)
            last_day = max(daily_counts)
            current = first_day
            ordered_days = []
            while current <= last_day:
                ordered_days.append(daily_counts.get(current, 0))
                current += timedelta(days=1)
            rolling_three_day = max(
                sum(ordered_days[max(0, index - 2) : index + 1])
                for index in range(len(ordered_days))
            )

        timeline = []
        for index, item in enumerate(reversed(trajectories), 1):
            contributions = []
            if item.caller_type:
                contributions.append(f"主体识别：{item.caller_type}")
            if item.core_question:
                contributions.append("形成具体咨询主题")
            if item.proficiency_score is not None:
                contributions.append(f"熟练度证据：{item.proficiency_score:.1f}/10")
            if item.resolved_status is False:
                contributions.append("进入未解决事项池")
            if item.is_repeated_issue is True:
                contributions.append("确认同一问题重复咨询")
            elif item.repeat_review_status == "pending_review":
                contributions.append("进入重复问题候选核对")
            if item.taxpayer_dissatisfied is True:
                contributions.append("形成服务关注信号")
            timeline.append(
                {
                    "index": len(trajectories) - index + 1,
                    "business_id": item.business_id,
                    "call_time": item.call_time,
                    "question": item.core_question or "咨询事项未形成明确记录",
                    "topic_category": item.topic_category,
                    "demand_category": item.demand_category,
                    "resolved": item.resolved_status,
                    "unresolved_reason": item.unresolved_reason,
                    "is_repeated_issue": item.is_repeated_issue,
                    "repeat_status": item.repeat_review_status,
                    "repeat_summary": item.repeat_summary,
                    "service_rating": item.service_rating,
                    "contributions": contributions or ["保留为基础来电事实"],
                }
            )

        metric_labels = {
            "total_calls": "累计来电",
            "repeated_issues": "同一问题重复咨询",
            "unresolved": "未直接解决",
            "work_orders": "历史工单",
            "dissatisfaction": "服务不满",
        }
        changes = [
            {
                "field": label,
                "before": state[field],
                "after": after_state[field],
                "changed": state[field] != after_state[field],
            }
            for field, label in metric_labels.items()
        ]
        changes.extend(
            [
                {
                    "field": "服务画像",
                    "before": before["profile_type"],
                    "after": after["profile_type"],
                    "changed": before["profile_type"] != after["profile_type"],
                },
                {
                    "field": "推荐服务方式",
                    "before": before["service_mode"],
                    "after": after["service_mode"],
                    "changed": before["service_mode"] != after["service_mode"],
                },
            ]
        )

        return {
            "profile_key": key,
            "masked_phone": masked_phone,
            "scenario": {**scenario_map[scenario_id], "event": scenario_event},
            "profile": {
                "caller_type": profile.caller_type,
                "enterprise_identity": profile.enterprise_identity,
                "latest_question": profile.latest_question,
                "topic_category": profile.latest_topic_category,
                "demand_category": profile.latest_demand_category,
                "registration_unit": profile.latest_registration_unit,
                "proficiency_score": profile.proficiency_score,
                "proficiency_summary": profile.proficiency_summary,
                "latest_service_rating": profile.latest_service_rating,
                "first_call_time": profile.first_call_time,
                "latest_call_time": profile.latest_call_time,
            },
            "rolling_signals": {
                "total_calls": profile.total_call_count,
                "same_direction_count": (
                    min(profile.total_call_count, profile.repeated_issue_count + 1)
                    if profile.repeated_issue_count
                    else 0
                ),
                "repeat_candidates": sum(
                    item.repeat_review_status == "pending_review"
                    for item in trajectories
                ),
                "max_single_day": max_single_day,
                "max_three_days": rolling_three_day,
            },
            "timeline": timeline,
            "before": {"state": state, "result": before},
            "after": {"state": after_state, "result": after},
            "changes": changes,
            "disclaimer": "该页面用于演示画像增量逻辑。模拟事件仅在本次页面请求中计算，不写入画像库或来电轨迹。",
        }

    def history_page(
        self, *, page: object = 1, page_size: object = 10, phone: object | None = None
    ) -> dict[str, object]:
        """Return one newest-first page of call trajectories with masked numbers."""

        try:
            page_number = int(page)
            size = int(page_size)
        except (TypeError, ValueError) as exc:
            raise ValueError("分页参数必须是整数") from exc
        if page_number < 1:
            raise ValueError("页码必须大于等于 1")
        if not 1 <= size <= 50:
            raise ValueError("每页条数必须在 1 至 50 之间")

        phone_hash: str | None = None
        if phone is not None and str(phone).strip():
            phone_hash = self.protector.hash_phone(phone)

        filters = (
            (CallTrajectory.phone_hash == phone_hash,) if phone_hash is not None else ()
        )
        with self._sessions() as session:
            total = session.scalar(
                select(func.count()).select_from(CallTrajectory).where(*filters)
            ) or 0
            trajectories = session.scalars(
                select(CallTrajectory)
                .where(*filters)
                .order_by(
                    CallTrajectory.call_time.desc(),
                    CallTrajectory.business_id.desc(),
                )
                .offset((page_number - 1) * size)
                .limit(size)
            ).all()
            hashes = {item.phone_hash for item in trajectories}
            profiles = {
                item.phone_hash: item
                for item in session.scalars(
                    select(CallerProfile).where(CallerProfile.phone_hash.in_(hashes))
                ).all()
            }

        items: list[dict[str, object]] = []
        for item in trajectories:
            profile = profiles.get(item.phone_hash)
            masked_phone = "号码信息不可用"
            if profile is not None:
                try:
                    masked_phone = _mask_phone(
                        self.protector.decrypt_phone(profile.phone_encrypted)
                    )
                except (ValueError, TypeError):
                    pass
            items.append(
                {
                    "masked_phone": masked_phone,
                    "business_id": item.business_id,
                    "call_time": item.call_time,
                    "caller_type": item.caller_type,
                    "enterprise_identity": item.enterprise_identity,
                    "core_question": item.core_question,
                    "question_category": item.topic_category,
                    "demand_category": item.demand_category,
                    "registration_unit": item.registration_unit,
                    "resolved": item.resolved_status,
                    "unresolved_reason": item.unresolved_reason,
                    "work_order": item.work_order,
                    "is_repeated_call": item.is_repeated_call,
                    "is_repeated_issue": item.is_repeated_issue,
                    "service_rating": item.service_rating,
                    "analysis_status": item.analysis_status,
                }
            )
        return {
            "page": page_number,
            "page_size": size,
            "total": total,
            "total_pages": ceil(total / size) if total else 0,
            "filtered": phone_hash is not None,
            "items": items,
        }

    def history_detail(self, business_id: object) -> dict[str, object] | None:
        identifier = str(business_id).strip()
        if not identifier:
            raise ValueError("缺少业务编号")
        with self._sessions() as session:
            item = session.get(CallTrajectory, identifier)
        if item is None:
            return None
        masked_phone = "号码信息不可用"
        if item.raw_phone_encrypted:
            try:
                masked_phone = _mask_phone(
                    self.protector.decrypt_phone(item.raw_phone_encrypted)
                )
            except (ValueError, TypeError):
                pass
        return {
            "original": {
                "business_id": item.business_id,
                "transcript": item.raw_transcript,
                "registration_time": item.registration_time,
                "call_start_time": item.raw_call_start_time,
                "call_end_time": item.call_end_time,
                "agent_id": item.agent_id,
                "agent_name": item.agent_name,
                "business_content": item.business_content,
                "answer_content": item.answer_content,
                "recording_path": item.recording_path,
                "registration_unit": item.registration_unit,
                "handling_method": item.handling_method,
                "business_category": item.business_category,
                "masked_phone": masked_phone,
                "satisfaction": item.satisfaction,
                "call_serial_number": item.call_serial_number,
            },
            "extracted": {
                "caller_type": item.caller_type,
                "detailed_subject": item.enterprise_identity,
                "core_question": item.core_question,
                "topic_category": item.topic_category,
                "demand_category": item.demand_category,
                "resolved": item.resolved_status,
                "unresolved_reason": item.unresolved_reason,
                "work_order": item.work_order,
                "proficiency_score": item.proficiency_score,
                "proficiency_summary": item.proficiency_summary,
                "service_effect_rating": item.service_rating,
                "service_effect_summary": item.service_summary,
                "is_repeated_issue": item.is_repeated_issue,
                "repeat_reason": item.repeat_reason,
                "matched_previous_question": item.matched_previous_question,
                "matched_previous_call_time": item.matched_previous_call_time,
                "previous_issue_resolved": item.previous_issue_resolved,
                "repeat_summary": item.repeat_summary,
                "repeat_confidence": item.repeat_confidence,
                "repeat_review_status": item.repeat_review_status,
                "contact_target": item.contact_target,
                "analysis_status": item.analysis_status,
            },
        }


def _counter_rows(
    counter: Counter[str], limit: int | None = None
) -> list[dict[str, object]]:
    rows = counter.most_common(limit)
    return [{"label": label, "value": value} for label, value in rows]


def _segmented_counter_rows(
    counter: Counter[str],
    resolution: dict[str, Counter[str]],
    limit: int | None = None,
) -> list[dict[str, object]]:
    return [
        {
            "label": label,
            "value": value,
            "resolved": resolution[label]["resolved"],
            "unresolved": resolution[label]["unresolved"],
            "unknown": resolution[label]["unknown"],
        }
        for label, value in counter.most_common(limit)
    ]


def _mask_phone(phone: str) -> str:
    if len(phone) >= 8:
        return f"{phone[:3]}{'*' * (len(phone) - 7)}{phone[-4:]}"
    if len(phone) >= 5:
        return f"{phone[:2]}{'*' * (len(phone) - 4)}{phone[-2:]}"
    if len(phone) >= 3:
        return f"{phone[0]}{'*' * (len(phone) - 2)}{phone[-1:]}"
    return "*" * len(phone)


def _handler_factory(service: DemoService) -> type[BaseHTTPRequestHandler]:
    class DemoHandler(BaseHTTPRequestHandler):
        server_version = "TaxpayerProfileDemo/0.1"

        def _json(self, status: int, payload: object) -> None:
            content = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"error": message})

        def _read_json(self) -> dict[str, object]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("请求长度无效") from exc
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("请求内容为空或过大")
            try:
                body = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError("请求必须是有效 JSON") from exc
            if not isinstance(body, dict):
                raise ValueError("请求内容必须是 JSON 对象")
            return body

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                content = (WEB_ROOT / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)
                return
            if parsed.path == "/api/dashboard":
                try:
                    self._json(200, service.dashboard_summary())
                except ValueError as exc:
                    self._error(400, str(exc))
                return
            if parsed.path == "/api/showcase/catalog":
                try:
                    self._json(200, service.profile_showcase_catalog())
                except ValueError as exc:
                    self._error(400, str(exc))
                return
            self._error(404, "接口不存在")

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path not in {
                "/api/profile",
                "/api/advice",
                "/api/history",
                "/api/history/detail",
                "/api/showcase",
            }:
                self._error(404, "接口不存在")
                return
            try:
                body = self._read_json()
            except ValueError as exc:
                self._error(400, str(exc))
                return
            try:
                if path == "/api/profile":
                    phone = body.get("phone")
                    if phone is None:
                        raise ValueError("缺少来电号码")
                    profile = service.lookup_profile(phone)
                    self._json(200, {"found": profile is not None, "profile": profile})
                elif path == "/api/advice":
                    phone = body.get("phone")
                    if phone is None:
                        raise ValueError("缺少来电号码")
                    self._json(200, service.generate_advice(phone))
                elif path == "/api/history":
                    self._json(
                        200,
                        service.history_page(
                            page=body.get("page", 1),
                            page_size=body.get("page_size", 10),
                            phone=body.get("phone"),
                        ),
                    )
                elif path == "/api/history/detail":
                    business_id = body.get("business_id")
                    if business_id is None:
                        raise ValueError("缺少业务编号")
                    detail = service.history_detail(business_id)
                    self._json(200, {"found": detail is not None, "detail": detail})
                else:
                    self._json(
                        200,
                        service.profile_showcase(
                            profile_key=body.get("profile_key"),
                            scenario=body.get("scenario", "baseline"),
                        ),
                    )
            except ValueError as exc:
                self._error(400, str(exc))

        def log_message(self, format: str, *args: object) -> None:
            del format, args
            print(f"{self.address_string()} - {self.command} {urlparse(self.path).path}")

    return DemoHandler


def run_server(
    *, service: DemoService, host: str = "127.0.0.1", port: int = 8000
) -> None:
    server = ThreadingHTTPServer((host, port), _handler_factory(service))
    print(f"坐席服务辅助系统已启动：http://{host}:{port}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

"""Reusable, explainable phone-profile queries for the web demo."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from taxpayer_profile.database import make_engine, make_session_factory
from taxpayer_profile.models import CallerProfile, CallTrajectory
from taxpayer_profile.profiles.aggregation import repeat_label_is_active
from taxpayer_profile.profiling import classify_reception_mode
from taxpayer_profile.security import PhoneProtector, normalize_phone
from taxpayer_profile.service_calendar import recent_service_days


def _five_workdays(anchor: date) -> list[date]:
    """Compatibility name for the five-day statutory service window."""

    return recent_service_days(anchor, count=5)


def _recent_service_items(trajectories: list[CallTrajectory]) -> list[CallTrajectory]:
    """Select one phone's calls in the latest five statutory workdays."""

    if not trajectories:
        return []
    allowed = set(_five_workdays(trajectories[0].call_time.date()))
    return [item for item in trajectories if item.call_time.date() in allowed]


def _abnormal(item: CallTrajectory) -> bool:
    return getattr(item, "rule_abnormal_end", None) is True or getattr(
        item, "model_abnormal_end", None
    ) is True


def _wait_pushback(item: CallTrajectory) -> bool:
    return getattr(item, "waiting_expression", None) is True and getattr(
        item, "potential_pushback", None
    ) is True


def _contact_unresolved(item: CallTrajectory) -> bool:
    return getattr(item, "contacted_other_department", None) is True and getattr(
        item, "resolved_status", None
    ) is False


def _agent_answer_summary(item: CallTrajectory) -> str:
    """Expose only the persisted model extraction; never synthesize it locally."""

    if item.agent_answer_summary:
        return item.agent_answer_summary
    if item.model_name:
        return "模型未识别出可明确提炼的坐席答复。"
    return "该通记录尚未完成坐席答复的模型提炼。"


def _recent_workday_statistics(
    trajectories: list[CallTrajectory],
) -> dict[str, Any]:
    latest = trajectories[0]
    workdays = _five_workdays(latest.call_time.date())
    recent = _recent_service_items(trajectories)
    return {
        "start_date": workdays[0].isoformat(),
        "end_date": workdays[-1].isoformat(),
        "call_count": len(recent),
        "repeated_issue_count": sum(repeat_label_is_active(item) for item in recent),
        "work_order_count": sum(item.work_order is True for item in recent),
        "wait_pushback_count": sum(_wait_pushback(item) for item in recent),
        "abnormal_end_count": sum(_abnormal(item) for item in recent),
        "contact_unresolved_count": sum(_contact_unresolved(item) for item in recent),
        "unresolved_count": sum(item.resolved_status is False for item in recent),
        "dissatisfaction_count": sum(
            item.taxpayer_dissatisfied is True for item in recent
        ),
    }


def _mode_payload(
    profile: CallerProfile, recent_stats: dict[str, Any]
) -> dict[str, Any]:
    result = classify_reception_mode(
        proficiency_level=profile.proficiency_level,
        emotion_state=profile.emotion_state,
        wait_pushback_count=int(recent_stats["wait_pushback_count"]),
        work_order_count=int(recent_stats["work_order_count"]),
        abnormal_end_count=int(recent_stats["abnormal_end_count"]),
        contact_unresolved_count=int(recent_stats["contact_unresolved_count"]),
        dissatisfaction_count=int(recent_stats["dissatisfaction_count"]),
    )
    return {
        "id": result.mode_id,
        "label": result.mode,
        "basis": result.basis,
        "focus": result.focus,
        "communication": result.communication,
        "avoid": result.avoid,
        "matched_facts": list(result.matched_facts),
        "components": [
            {
                "category_id": component.category_id,
                "category": component.category,
                "mode_id": component.mode_id,
                "mode": component.mode,
                "basis": component.basis,
                "focus": component.focus,
                "communication": component.communication,
                "avoid": component.avoid,
            }
            for component in result.components
        ],
    }


def _trajectory_context(item: CallTrajectory) -> dict[str, Any]:
    return {
        "business_id": item.business_id,
        "call_time": item.call_time.isoformat(sep=" "),
        "question": item.core_question,
        "question_category": item.father_question,
        "topic_category": item.topic_category,
        "secondary_topic": item.secondary_topic,
        "demand_category": item.demand_category,
        "agent_answer_summary": item.agent_answer_summary,
        "resolved": item.resolved_status,
        "unresolved_reason": item.unresolved_reason,
        "work_order": item.work_order,
        "repeated_issue": item.is_repeated_issue,
        "waiting": item.waiting_expression,
        "potential_pushback": item.potential_pushback,
        "wait_pushback": _wait_pushback(item),
        "dissatisfied": item.taxpayer_dissatisfied,
        "abnormal_end": _abnormal(item),
        "contacted_other_department": item.contacted_other_department,
        "contact_unresolved": _contact_unresolved(item),
        "contact_target": item.contact_target,
        "service_rating": item.service_rating,
        "proficiency_level": item.proficiency_level,
        "emotion_state": item.emotion_state,
    }


def build_agent_context(
    profile: CallerProfile,
    trajectories: list[CallTrajectory],
    *,
    history_limit: int = 8,
) -> dict[str, Any]:
    """Build a privacy-minimized context; reception mode is deterministic."""

    recent_stats = _recent_workday_statistics(trajectories)
    mode = _mode_payload(profile, recent_stats)
    return {
        "profile_summary": profile.profile_summary,
        "proficiency_level": profile.proficiency_level or "暂无法判断",
        "proficiency_basis": profile.proficiency_basis,
        "emotion_state": profile.emotion_state or "暂无法判断",
        "emotion_basis": profile.emotion_basis,
        "recommended_mode": mode["label"],
        "recommended_modes": mode["components"],
        "mode_basis": mode["basis"],
        "mode_guidance": {
            "focus": mode["focus"],
            "communication": mode["communication"],
            "avoid": mode["avoid"],
            "components": mode["components"],
        },
        "caller_type": profile.caller_type,
        "enterprise_identity": profile.enterprise_identity,
        "latest_topic_category": profile.latest_topic_category,
        "latest_demand_category": profile.latest_demand_category,
        "statistics": {
            "total_calls": profile.total_call_count,
            "repeated_calls": profile.repeated_call_count,
            "repeated_issues": profile.repeated_issue_count,
            "unresolved_calls": profile.unresolved_count,
            "work_orders": profile.work_order_count,
            "abnormal_ends": profile.abnormal_end_count,
            "dissatisfaction_calls": profile.dissatisfaction_count,
        },
        "recent_five_workdays": recent_stats,
        "recent_questions": profile.recent_questions_summary,
        "unresolved_questions": profile.unresolved_questions_summary,
        "repeated_questions": profile.repeated_questions_summary,
        "latest_resolved": profile.latest_resolved,
        "latest_unresolved_reason": profile.latest_unresolved_reason,
        "recent_trajectories": [
            _trajectory_context(item) for item in trajectories[:history_limit]
        ],
    }


def _full_trajectory(item: CallTrajectory) -> dict[str, Any]:
    return {
        "business_id": item.business_id,
        "call_time": item.call_time.isoformat(sep=" "),
        "registration_time": (
            item.registration_time.isoformat(sep=" ")
            if item.registration_time
            else None
        ),
        "call_end_time": (
            item.call_end_time.isoformat(sep=" ") if item.call_end_time else None
        ),
        "call_time_source": item.call_time_source,
        "core_question": item.core_question,
        "topic_category": item.topic_category,
        "secondary_topic": item.secondary_topic,
        "demand_category": item.demand_category,
        "agent_answer_summary": item.agent_answer_summary,
        "registration_unit": item.registration_unit,
        "natural_qa_turns": item.natural_qa_turns,
        "core_question_turns": item.core_question_turns,
        "effective_qa_turns": item.effective_qa_turns,
        "effective_qa_content": item.effective_qa_content,
        "resolved": item.resolved_status,
        "unresolved_reason": item.unresolved_reason,
        "work_order": item.work_order,
        "waiting_expression": item.waiting_expression,
        "potential_pushback": item.potential_pushback,
        "wait_pushback": _wait_pushback(item),
        "taxpayer_dissatisfied": item.taxpayer_dissatisfied,
        "rule_abnormal_end": item.rule_abnormal_end,
        "model_abnormal_end": item.model_abnormal_end,
        "abnormal_end": _abnormal(item),
        "contacted_other_department": item.contacted_other_department,
        "active_contacted_other_department": item.active_contacted_other_department,
        "contact_unresolved": _contact_unresolved(item),
        "contact_target": item.contact_target,
        "proficiency_level": item.proficiency_level,
        "proficiency_basis": item.proficiency_basis,
        "emotion_state": item.emotion_state,
        "emotion_basis": item.emotion_basis,
        "is_repeated_call": item.is_repeated_call,
        "is_repeated_issue": item.is_repeated_issue,
        "matched_previous_business_id": item.matched_previous_business_id,
        "matched_previous_question": item.matched_previous_question,
        "matched_previous_call_time": (
            item.matched_previous_call_time.isoformat(sep=" ")
            if item.matched_previous_call_time
            else None
        ),
        "previous_issue_resolved": item.previous_issue_resolved,
        "repeat_candidate_score": item.repeat_candidate_score,
        "repeat_confidence": item.repeat_confidence,
        "repeat_review_status": item.repeat_review_status,
        "repeat_reason": item.repeat_reason,
        "repeat_summary": item.repeat_summary,
        "service_rating": item.service_rating,
        "service_summary": item.service_summary,
        "analysis_status": item.analysis_status,
        "analysis_source": item.analysis_source,
        "analysis_version": item.analysis_version,
        "enterprise_identity_source": item.enterprise_identity_source,
    }


def query_profile(
    *, phone: object, database_path: Path | str, protector: PhoneProtector
) -> dict[str, Any] | None:
    """Compatibility entry point for scripts that only have a database path."""

    engine = make_engine(database_path)
    return query_profile_from_sessions(
        phone=phone,
        sessions=make_session_factory(engine),
        protector=protector,
    )


def query_profile_from_sessions(
    *,
    phone: object,
    sessions: Callable[[], Session],
    protector: PhoneProtector,
) -> dict[str, Any] | None:
    """Query one profile through an injected application session boundary."""

    normalized = normalize_phone(phone)
    if normalized is None:
        raise ValueError("来电号码必须为数字，可包含常见空格、横线或括号")
    phone_hash = protector.hash_phone(normalized)
    with sessions() as session:
        profile = session.get(CallerProfile, phone_hash)
        if profile is None:
            return None
        trajectories = list(
            session.scalars(
                select(CallTrajectory)
                .where(CallTrajectory.phone_hash == phone_hash)
                .order_by(CallTrajectory.call_time.desc())
            )
        )
    recent_stats = _recent_workday_statistics(trajectories)
    mode = _mode_payload(profile, recent_stats)
    serialized = [_full_trajectory(item) for item in trajectories]
    recent_items = _recent_service_items(trajectories)
    recent_serialized = [_full_trajectory(item) for item in recent_items]
    active_repeat_ids = {
        item.business_id for item in recent_items if repeat_label_is_active(item)
    }
    latest = trajectories[0]
    return {
        "caller_type": profile.caller_type,
        "enterprise_identity": profile.enterprise_identity,
        "proficiency_level": profile.proficiency_level or "暂无法判断",
        "proficiency_basis": profile.proficiency_basis,
        "emotion_state": profile.emotion_state or "暂无法判断",
        "emotion_basis": profile.emotion_basis,
        "recommended_mode": mode["label"],
        "recommended_modes": mode["components"],
        "reception_mode": mode,
        "first_call_time": profile.first_call_time.isoformat(sep=" "),
        "latest_call_time": profile.latest_call_time.isoformat(sep=" "),
        "total_call_count": profile.total_call_count,
        "repeated_call_count": profile.repeated_call_count,
        "repeated_issue_count": profile.repeated_issue_count,
        "unresolved_count": profile.unresolved_count,
        "work_order_count": profile.work_order_count,
        "abnormal_end_count": profile.abnormal_end_count,
        "dissatisfaction_count": profile.dissatisfaction_count,
        "latest_question": profile.latest_question,
        "latest_agent_answer": _agent_answer_summary(latest),
        "standard_answer": "知识库暂未接入，后续将根据当前诉求实时检索。",
        "latest_topic_category": profile.latest_topic_category,
        "latest_demand_category": profile.latest_demand_category,
        "latest_registration_unit": profile.latest_registration_unit,
        "latest_father_question": profile.latest_father_question,
        "latest_resolved": profile.latest_resolved,
        "latest_unresolved_reason": profile.latest_unresolved_reason,
        "profile_summary": profile.profile_summary,
        "recent_questions_summary": profile.recent_questions_summary,
        "unresolved_questions_summary": profile.unresolved_questions_summary,
        "repeated_questions_summary": profile.repeated_questions_summary,
        "recent_workday_statistics": recent_stats,
        "history_focus": {
            "repeated_issues": [
                item
                for item in recent_serialized
                if item["business_id"] in active_repeat_ids
            ],
            "work_orders": [item for item in recent_serialized if item["work_order"] is True],
            "contact_unresolved": [
                item
                for item in recent_serialized
                if item["contact_unresolved"] is True
            ],
            "dissatisfaction": [
                item
                for item in recent_serialized
                if item["taxpayer_dissatisfied"] is True
            ],
            "unresolved": [
                item for item in recent_serialized if item["resolved"] is False
            ],
        },
        "agent_context": build_agent_context(profile, trajectories),
        "trajectories": serialized,
    }

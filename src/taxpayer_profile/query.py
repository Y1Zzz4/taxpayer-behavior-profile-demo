"""Reusable, explainable phone-profile queries for the web demo."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from taxpayer_profile.database import make_engine, make_session_factory
from taxpayer_profile.models import CallerProfile, CallTrajectory
from taxpayer_profile.profiling import classify_reception_mode
from taxpayer_profile.security import PhoneProtector, normalize_phone


def _five_workdays(anchor: date) -> list[date]:
    days: list[date] = []
    current = anchor
    while len(days) < 5:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return sorted(days)


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


def _recent_workday_statistics(
    trajectories: list[CallTrajectory],
) -> dict[str, Any]:
    latest = trajectories[0]
    workdays = _five_workdays(latest.call_time.date())
    allowed = set(workdays)
    recent = [item for item in trajectories if item.call_time.date() in allowed]
    latest_demands = {
        label.strip()
        for label in (latest.demand_category or "").replace("，", ",").split(",")
        if label.strip()
    }
    if latest_demands:
        same_kind = sum(
            bool(
                latest_demands
                & {
                    label.strip()
                    for label in (item.demand_category or "")
                    .replace("，", ",")
                    .split(",")
                    if label.strip()
                }
            )
            for item in recent
        )
    elif latest.topic_category:
        same_kind = sum(
            item.topic_category == latest.topic_category for item in recent
        )
    else:
        same_kind = 0
    return {
        "start_date": workdays[0].isoformat(),
        "end_date": workdays[-1].isoformat(),
        "call_count": len(recent),
        "same_demand_count": same_kind,
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
    }


def _trajectory_context(item: CallTrajectory) -> dict[str, Any]:
    return {
        "business_id": item.business_id,
        "call_time": item.call_time.isoformat(sep=" "),
        "question": item.core_question,
        "question_category": item.father_question,
        "topic_category": item.topic_category,
        "demand_category": item.demand_category,
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
        "mode_basis": mode["basis"],
        "mode_guidance": {
            "focus": mode["focus"],
            "communication": mode["communication"],
            "avoid": mode["avoid"],
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
        "demand_category": item.demand_category,
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
    normalized = normalize_phone(phone)
    if normalized is None:
        raise ValueError("来电号码必须为数字，可包含常见空格、横线或括号")
    phone_hash = protector.hash_phone(normalized)
    engine = make_engine(database_path)
    sessions = make_session_factory(engine)
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
    return {
        "caller_type": profile.caller_type,
        "enterprise_identity": profile.enterprise_identity,
        "proficiency_level": profile.proficiency_level or "暂无法判断",
        "proficiency_basis": profile.proficiency_basis,
        "emotion_state": profile.emotion_state or "暂无法判断",
        "emotion_basis": profile.emotion_basis,
        "recommended_mode": mode["label"],
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
            "same_demand": [
                item for item in serialized if item["is_repeated_issue"] is True
            ],
            "work_orders": [item for item in serialized if item["work_order"] is True],
            "wait_pushback": [
                item for item in serialized if item["wait_pushback"] is True
            ],
            "dissatisfaction": [
                item
                for item in serialized
                if item["taxpayer_dissatisfied"] is True
            ],
            "unresolved": [
                item for item in serialized if item["resolved"] is False
            ],
        },
        "agent_context": build_agent_context(profile, trajectories),
        "trajectories": serialized,
    }

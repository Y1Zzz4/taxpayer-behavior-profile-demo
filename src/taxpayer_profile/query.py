"""Reusable phone-profile query service for CLI and future web agents."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from taxpayer_profile.database import make_engine, make_session_factory
from taxpayer_profile.models import CallerProfile, CallTrajectory
from taxpayer_profile.security import PhoneProtector, normalize_phone


def _five_workdays(anchor: date) -> list[date]:
    days: list[date] = []
    current = anchor
    while len(days) < 5:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return sorted(days)


def _recent_workday_statistics(
    trajectories: list[CallTrajectory],
) -> dict[str, Any]:
    latest = trajectories[0]
    workdays = _five_workdays(latest.call_time.date())
    allowed = set(workdays)
    recent = [item for item in trajectories if item.call_time.date() in allowed]
    latest_demands = {
        label.strip()
        for label in (latest.demand_category or "").split(",")
        if label.strip()
    }
    if latest_demands:
        same_kind = sum(
            bool(
                latest_demands
                & {
                    label.strip()
                    for label in (item.demand_category or "").split(",")
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
        "unresolved_count": sum(item.resolved_status is False for item in recent),
        "dissatisfaction_count": sum(
            item.taxpayer_dissatisfied is True for item in recent
        ),
    }


def build_agent_context(
    profile: CallerProfile, trajectories: list[CallTrajectory], *, history_limit: int = 5
) -> dict[str, Any]:
    """Build a privacy-minimized, factual context for real-time service advice."""

    recent_history = trajectories[:history_limit]
    return {
        "profile_summary": profile.profile_summary,
        "service_profile_type": profile.service_profile_type,
        "service_profile_basis": profile.service_profile_basis,
        "caller_type": profile.caller_type,
        "enterprise_identity": profile.enterprise_identity,
        "latest_topic_category": profile.latest_topic_category,
        "latest_demand_category": profile.latest_demand_category,
        "proficiency_score": profile.proficiency_score,
        "proficiency_summary": profile.proficiency_summary,
        "statistics": {
            "total_calls": profile.total_call_count,
            "repeated_calls": profile.repeated_call_count,
            "repeated_issues": profile.repeated_issue_count,
            "unresolved_calls": profile.unresolved_count,
            "work_orders": profile.work_order_count,
            "abnormal_ends": profile.abnormal_end_count,
            "dissatisfaction_calls": profile.dissatisfaction_count,
        },
        "recent_questions": profile.recent_questions_summary,
        "unresolved_questions": profile.unresolved_questions_summary,
        "repeated_questions": profile.repeated_questions_summary,
        "latest_resolved": profile.latest_resolved,
        "latest_unresolved_reason": profile.latest_unresolved_reason,
        "latest_service_rating": profile.latest_service_rating,
        "recent_trajectories": [
            {
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
                "dissatisfied": item.taxpayer_dissatisfied,
                "abnormal_end": bool(
                    item.rule_abnormal_end is True
                    or item.model_abnormal_end is True
                ),
                "service_rating": item.service_rating,
            }
            for item in recent_history
        ],
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
        return {
            "caller_type": profile.caller_type,
            "enterprise_identity": profile.enterprise_identity,
            "service_profile_type": profile.service_profile_type,
            "service_profile_basis": profile.service_profile_basis,
            "proficiency_score": profile.proficiency_score,
            "proficiency_summary": profile.proficiency_summary,
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
            "latest_service_rating": profile.latest_service_rating,
            "profile_summary": profile.profile_summary,
            "recent_questions_summary": profile.recent_questions_summary,
            "unresolved_questions_summary": profile.unresolved_questions_summary,
            "repeated_questions_summary": profile.repeated_questions_summary,
            "recent_workday_statistics": _recent_workday_statistics(trajectories),
            "agent_context": build_agent_context(profile, trajectories),
            "trajectories": [
                {
                    "business_id": item.business_id,
                    "call_time": item.call_time.isoformat(sep=" "),
                    "registration_time": (
                        item.registration_time.isoformat(sep=" ")
                        if item.registration_time
                        else None
                    ),
                    "call_end_time": (
                        item.call_end_time.isoformat(sep=" ")
                        if item.call_end_time
                        else None
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
                    "is_repeated_call": item.is_repeated_call,
                    "is_repeated_issue": item.is_repeated_issue,
                    "repeat_candidate_score": item.repeat_candidate_score,
                    "repeat_confidence": item.repeat_confidence,
                    "repeat_review_status": item.repeat_review_status,
                    "repeat_reason": item.repeat_reason,
                    "service_rating": item.service_rating,
                    "service_summary": item.service_summary,
                    "analysis_status": item.analysis_status,
                    "analysis_source": item.analysis_source,
                    "analysis_version": item.analysis_version,
                    "enterprise_identity_source": item.enterprise_identity_source,
                }
                for item in trajectories
            ],
        }

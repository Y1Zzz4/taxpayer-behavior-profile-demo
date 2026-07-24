"""Deterministically aggregate call trajectories into a caller profile.

This module deliberately has no workbook, model-client or database-session
dependency. A caller profile is a materialized view of its persisted call
history and can therefore be rebuilt safely whenever aggregation rules change.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from taxpayer_profile.models import CallerProfile, CallTrajectory
from taxpayer_profile.profiling import (
    normalize_proficiency_level,
    weighted_proficiency,
)


def trajectory_key(trajectory: CallTrajectory) -> tuple[datetime, str]:
    """Return the stable chronological order used throughout ingestion."""

    return trajectory.call_time, trajectory.business_id


def repeat_label_is_active(trajectory: CallTrajectory) -> bool:
    """Apply expiry semantics to a stored repeated-issue decision."""

    if trajectory.is_repeated_issue is not True:
        return False
    expires_at = trajectory.repeat_label_expires_at
    if expires_at is None:
        return True
    # SQLite returns naive datetimes for the current schema. Comparing in the
    # same representation avoids mixing aware and naive datetime instances.
    comparable = expires_at.replace(tzinfo=None)
    return comparable > datetime.now(timezone.utc).replace(tzinfo=None)


def _recent_unique_questions(
    trajectories: list[CallTrajectory],
    *,
    limit: int = 3,
    unresolved_only: bool = False,
    repeated_only: bool = False,
) -> list[str]:
    questions: list[str] = []
    for item in reversed(trajectories):
        if unresolved_only and item.resolved_status is not False:
            continue
        if repeated_only and not repeat_label_is_active(item):
            continue
        question = item.core_question or item.father_question
        if not question or question in questions:
            continue
        questions.append(question)
        if len(questions) >= limit:
            break
    return questions


def _question_summary(questions: list[str]) -> str | None:
    if not questions:
        return None
    return "；".join(
        f"{index}. {question}" for index, question in enumerate(questions, 1)
    )


def _unresolved_summary(
    trajectories: list[CallTrajectory], limit: int = 3
) -> str | None:
    rows: list[str] = []
    for item in reversed(trajectories):
        if item.resolved_status is not False:
            continue
        question = item.core_question or item.topic_category or "该次咨询事项"
        reason = item.unresolved_reason or "未直接解决原因未形成明确记录"
        rendered = f"{question}（原因：{reason}）"
        if rendered not in rows:
            rows.append(rendered)
        if len(rows) >= limit:
            break
    return _question_summary(rows)


def _personalized_profile_summary(profile: CallerProfile) -> str:
    sentences: list[str] = []
    if profile.caller_type == "企业":
        if profile.enterprise_identity in {None, "无法判断"}:
            sentences.append("最近一次为企业咨询，细化主体暂无法判断。")
        else:
            sentences.append(f"最近一次为企业咨询，细化主体为{profile.enterprise_identity}。")
    elif profile.caller_type == "个人":
        sentences.append("最近一次以个人身份咨询。")
    if profile.latest_question:
        categories = [
            value
            for value in (profile.latest_topic_category, profile.latest_demand_category)
            if value
        ]
        suffix = f"，归入{'、'.join(categories)}" if categories else ""
        sentences.append(f"最近关注“{profile.latest_question}”{suffix}。")
    if profile.proficiency_level:
        sentences.append(
            f"近期沟通显示其业务专业度为{profile.proficiency_level}，"
            f"{profile.proficiency_basis or '建议在本次接待中继续观察理解程度'}"
        )
    if profile.emotion_state:
        sentences.append(
            f"近期情绪状态为{profile.emotion_state}，"
            f"{profile.emotion_basis or '建议根据本次表达动态调整沟通方式'}"
        )
    if profile.unresolved_count:
        reason = profile.latest_unresolved_reason or "仍需核对具体处理节点"
        sentences.append(
            f"历史有{profile.unresolved_count}次未直接解决，最近原因是“{reason}”。"
        )
    elif profile.latest_service_rating:
        sentences.append(f"最近一次服务效果评估为“{profile.latest_service_rating}”。")
    return "".join(sentences)


def _recent_five_workday_items(
    ordered: list[CallTrajectory],
) -> list[CallTrajectory]:
    """Return calls in the five weekdays ending at the latest call date.

    This is intentionally a weekday window rather than a holiday calendar. It
    preserves the established business rule until an authoritative holiday
    source is introduced.
    """

    anchor = ordered[-1].call_time.date()
    workdays: set[date] = set()
    current = anchor
    while len(workdays) < 5:
        if current.weekday() < 5:
            workdays.add(current)
        current -= timedelta(days=1)
    return [item for item in ordered if item.call_time.date() in workdays]


def update_profile(
    *, profile: CallerProfile, trajectories: list[CallTrajectory]
) -> None:
    """Rebuild a materialized caller profile from a non-empty call history."""

    if not trajectories:
        raise ValueError("画像聚合需要至少一条来电轨迹")

    ordered = sorted(trajectories, key=trajectory_key)
    latest = ordered[-1]
    profile.first_call_time = ordered[0].call_time
    profile.latest_call_time = latest.call_time
    profile.total_call_count = len(ordered)
    profile.repeated_call_count = sum(item.is_repeated_call for item in ordered)
    profile.repeated_issue_count = sum(repeat_label_is_active(item) for item in ordered)
    profile.unresolved_count = sum(item.resolved_status is False for item in ordered)
    profile.work_order_count = sum(item.work_order is True for item in ordered)
    profile.abnormal_end_count = sum(
        item.rule_abnormal_end is True or item.model_abnormal_end is True
        for item in ordered
    )
    profile.dissatisfaction_count = sum(
        item.taxpayer_dissatisfied is True for item in ordered
    )
    if latest.caller_type not in {"企业", "个人"}:
        raise ValueError(
            f"业务编号 {latest.business_id} 的咨询主体必须为企业或个人"
        )
    profile.caller_type = latest.caller_type
    if profile.caller_type == "个人":
        profile.enterprise_identity = "不适用"
    elif profile.caller_type == "企业":
        profile.enterprise_identity = latest.enterprise_identity or "无法判断"
    profile.proficiency_score = weighted_proficiency(
        [(item.call_time, item.proficiency_score) for item in ordered]
    )
    profile.proficiency_summary = next(
        (
            item.proficiency_summary
            for item in reversed(ordered)
            if item.proficiency_score is not None
        ),
        "无法判断",
    )
    recent = _recent_five_workday_items(ordered)
    recent_level = next(
        (
            item
            for item in reversed(recent)
            if normalize_proficiency_level(
                item.proficiency_level, item.proficiency_score
            )
            != "暂无法判断"
        ),
        latest,
    )
    profile.proficiency_level = normalize_proficiency_level(
        recent_level.proficiency_level, recent_level.proficiency_score
    )
    profile.proficiency_basis = (
        recent_level.proficiency_basis
        or recent_level.proficiency_summary
        or "近期可用表达不足，暂不预设业务熟悉程度。"
    )
    recent_emotion = next(
        (
            item
            for item in reversed(recent)
            if item.emotion_state in {"平稳", "焦虑", "不满"}
        ),
        latest,
    )
    profile.emotion_state = recent_emotion.emotion_state or "暂无法判断"
    profile.emotion_basis = (
        recent_emotion.emotion_basis or "近期可用表达不足，暂不预设情绪状态。"
    )
    profile.latest_business_id = latest.business_id
    profile.latest_question = latest.core_question
    profile.latest_topic_category = latest.topic_category
    profile.latest_demand_category = latest.demand_category
    profile.latest_registration_unit = latest.registration_unit
    profile.latest_father_question = latest.father_question
    profile.latest_resolved = latest.resolved_status
    profile.latest_unresolved_reason = next(
        (
            item.unresolved_reason
            for item in reversed(ordered)
            if item.resolved_status is False and item.unresolved_reason
        ),
        None,
    )
    profile.latest_service_rating = latest.service_rating
    recent_questions = _recent_unique_questions(ordered)
    repeated_questions = _recent_unique_questions(ordered, repeated_only=True)
    profile.recent_questions_summary = _question_summary(recent_questions)
    profile.unresolved_questions_summary = _unresolved_summary(ordered)
    profile.repeated_questions_summary = _question_summary(repeated_questions)
    # These legacy columns remain nullable for database compatibility; current
    # reception modes are derived on read from the normalized profile facts.
    profile.service_profile_type = None
    profile.service_profile_basis = None
    profile.profile_summary = _personalized_profile_summary(profile)
    profile.updated_at = datetime.now(timezone.utc)

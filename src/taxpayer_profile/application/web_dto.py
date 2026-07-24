"""Pure response projections shared by web-facing application queries."""

from __future__ import annotations

import re
from collections import Counter
from typing import TypedDict

from taxpayer_profile.models import CallTrajectory


class HistoryDetailPayload(TypedDict):
    """Stable top-level contract returned by the history detail use case."""

    original: dict[str, object]
    extracted: dict[str, object]


def abnormal_end(item: CallTrajectory) -> bool:
    return item.rule_abnormal_end is True or item.model_abnormal_end is True


def wait_pushback(item: CallTrajectory) -> bool:
    return item.waiting_expression is True and item.potential_pushback is True


def contact_unresolved(item: CallTrajectory) -> bool:
    return item.contacted_other_department is True and item.resolved_status is False


def counter_rows(
    counter: Counter[str], limit: int | None = None
) -> list[dict[str, object]]:
    return [{"label": label, "value": value} for label, value in counter.most_common(limit)]


def segmented_rows(
    counter: Counter[str],
    resolution: dict[str, Counter[str]],
    *,
    limit: int = 5,
    exclude_other: bool = False,
) -> list[dict[str, object]]:
    total = sum(counter.values())
    ranked = (
        (label, value)
        for label, value in counter.most_common()
        if not exclude_other
        or label.strip().lower() not in {"其他", "其它", "其他类", "其它类"}
    )
    return [
        {
            "label": label,
            "value": value,
            "resolved": resolution[label]["resolved"],
            "unresolved": resolution[label]["unresolved"],
            "unknown": resolution[label]["unknown"],
            "share": round(value * 100 / total, 1) if total else 0,
            "resolved_share": round(
                resolution[label]["resolved"] * 100 / value, 1
            ) if value else 0,
            "unresolved_share": round(
                resolution[label]["unresolved"] * 100 / value, 1
            ) if value else 0,
            "unknown_share": round(
                resolution[label]["unknown"] * 100 / value, 1
            ) if value else 0,
        }
        for label, value in list(ranked)[:limit]
    ]


def split_labels(value: str | None, *, fallback: str) -> list[str]:
    labels = [
        part.strip()
        for part in re.split(r"[,，;；]+", value or "")
        if part.strip()
    ]
    return list(dict.fromkeys(labels)) or [fallback]


def resolution_state(item: CallTrajectory) -> str:
    if item.resolved_status is True:
        return "resolved"
    if item.resolved_status is False:
        return "unresolved"
    return "unknown"


def resolution_rows(items: list[CallTrajectory]) -> list[dict[str, object]]:
    counts = Counter(
        "已直接解决"
        if item.resolved_status is True
        else "未直接解决"
        if item.resolved_status is False
        else "状态待判定"
        for item in items
    )
    return [
        {"label": label, "value": counts[label]}
        for label in ("已直接解决", "未直接解决", "状态待判定")
        if counts[label]
    ]


def district_unit_label(value: str | None) -> str:
    label = (value or "").strip() or "登记单位待识别"
    display_names = {
        "上海市税务局": "中心",
        "国家税务总局上海市税务局": "中心",
        "第三税务分局": "三分局",
        "第三分局": "三分局",
        "自贸区分局": "自贸区",
        "浦东新区税务局": "浦东",
        "奉贤区税务局": "奉贤",
        "闵行区税务局": "闵行",
        "宝山区税务局": "宝山",
        "金山区税务局": "金山",
        "长宁区税务局": "长宁",
        "崇明区税务局": "崇明",
        "普陀区税务局": "普陀",
        "杨浦区税务局": "杨浦",
        "静安区税务局": "静安",
        "松江区税务局": "松江",
        "嘉定区税务局": "嘉定",
        "青浦区税务局": "青浦",
        "徐汇区税务局": "徐汇",
        "虹口区税务局": "虹口",
        "黄浦区税务局": "黄浦",
    }
    return display_names.get(label, label)


def secondary_labels_for_topic(
    topic: str, topics: list[str], secondary_labels: list[str]
) -> list[str]:
    if secondary_labels == ["二级专题待识别"]:
        return secondary_labels
    matched = [
        label
        for label in secondary_labels
        if label == topic or label.startswith(f"{topic}-")
    ]
    if not matched and len(topics) == 1:
        matched = secondary_labels
    if not matched:
        return ["二级专题待识别"]
    rendered = [
        label.removeprefix(f"{topic}-").strip() or topic for label in matched
    ]
    return list(dict.fromkeys(rendered))


def mask_phone(phone: str) -> str:
    if len(phone) >= 8:
        return f"{phone[:3]}{'*' * (len(phone) - 7)}{phone[-4:]}"
    if len(phone) >= 5:
        return f"{phone[:2]}{'*' * (len(phone) - 4)}{phone[-2:]}"
    if len(phone) >= 3:
        return f"{phone[0]}{'*' * (len(phone) - 2)}{phone[-1:]}"
    return "*" * len(phone)


def history_detail_payload(
    item: CallTrajectory, masked_phone: str
) -> HistoryDetailPayload:
    """Project one persistence entity without exposing encryption metadata."""

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
            "secondary_topic": item.secondary_topic,
            "demand_category": item.demand_category,
            "agent_answer_summary": item.agent_answer_summary,
            "resolved": item.resolved_status,
            "unresolved_reason": item.unresolved_reason,
            "work_order": item.work_order,
            "wait_pushback": wait_pushback(item),
            "abnormal_end": abnormal_end(item),
            "contact_unresolved": contact_unresolved(item),
            "taxpayer_dissatisfied": item.taxpayer_dissatisfied,
            "proficiency_level": item.proficiency_level,
            "proficiency_basis": item.proficiency_basis,
            "emotion_state": item.emotion_state,
            "emotion_basis": item.emotion_basis,
            "is_repeated_issue": item.is_repeated_issue,
            "repeat_reason": item.repeat_reason,
            "matched_previous_question": item.matched_previous_question,
            "previous_issue_resolved": item.previous_issue_resolved,
            "repeat_summary": item.repeat_summary,
            "repeat_review_status": item.repeat_review_status,
            "analysis_status": item.analysis_status,
        },
    }

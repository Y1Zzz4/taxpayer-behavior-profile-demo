"""Shared domain projections for dashboard and profile-showcase queries.

These helpers deliberately contain no database access.  Keeping profile facts and
reception-mode projection here prevents the two read use cases from gradually
developing different definitions of the same five-workday window.
"""

from __future__ import annotations

import hashlib

from taxpayer_profile.application.web_dto import (
    abnormal_end,
    contact_unresolved,
    wait_pushback,
)
from taxpayer_profile.models import CallTrajectory
from taxpayer_profile.profiling import classify_reception_mode
from taxpayer_profile.service_calendar import recent_service_days

PROFILE_DIMENSION_TAXONOMY = (
    {
        "id": "proficiency",
        "name": "业务专业度",
        "description": "根据业务表达、术语理解和办理节点认知，调整解释深度。",
        "categories": ("专业", "了解", "小白"),
        "unknown": "暂无法判断",
    },
    {
        "id": "emotion",
        "name": "近期情绪状态",
        "description": "只描述文本中可观察到的近期表达，不评价性格或心理。",
        "categories": ("平稳", "焦虑", "不满"),
        "unknown": "暂无法判断",
    },
    {
        "id": "facts",
        "name": "历史服务事实",
        "description": "按该号码最近五个工作日的明确字段组合形成，可同时命中多项。",
        "categories": (
            "历史工单",
            "联系后未解决",
            "异常中断",
            "等待推诿",
            "对坐席不满",
        ),
        "unknown": "近五个工作日未命中",
    },
)

HISTORICAL_FACT_DEFINITIONS = (
    {"id": "work_order", "label": "历史工单", "rule": "是否工单 = 是"},
    {
        "id": "contact_unresolved",
        "label": "联系相关人员后仍未解决",
        "rule": "联系相关人员或部门 = 是，且坐席是否解决纳税人问题 = 否",
    },
    {
        "id": "abnormal_end",
        "label": "异常中断",
        "rule": "采用原始分析的最终非正常中断字段；新增数据按同口径分析",
    },
    {
        "id": "wait_pushback",
        "label": "等待推诿",
        "rule": "存在让纳税人等待表述 = 是，且坐席存在潜在推诿行为 = 是",
    },
    {
        "id": "dissatisfaction",
        "label": "对坐席不满",
        "rule": "纳税人是否对当前坐席或本通热线存在不满 = 是",
    },
)


def showcase_key(phone_hash: str) -> str:
    """Produce an opaque, stable browser identifier without exposing phone hashes."""

    return hashlib.sha256(f"profile-showcase:{phone_hash}".encode()).hexdigest()[:18]


def fact_counts(items: list[CallTrajectory]) -> dict[str, int]:
    """Count the five historical facts under their published definitions."""

    return {
        "wait_pushback": sum(wait_pushback(item) for item in items),
        "work_order": sum(item.work_order is True for item in items),
        "abnormal_end": sum(abnormal_end(item) for item in items),
        "contact_unresolved": sum(contact_unresolved(item) for item in items),
        "dissatisfaction": sum(item.taxpayer_dissatisfied is True for item in items),
    }


def recent_five_workday_items(
    items: list[CallTrajectory],
) -> list[CallTrajectory]:
    """Restrict a phone's facts to the latest five statutory workdays."""

    if not items:
        return []
    anchor = max(item.call_time.date() for item in items)
    workdays = set(recent_service_days(anchor, count=5))
    return [item for item in items if item.call_time.date() in workdays]


def mode_payload(state: dict[str, object]) -> dict[str, object]:
    """Render deterministic reception-mode data from a compact profile state."""

    mode = classify_reception_mode(
        proficiency_level=str(state.get("proficiency_level") or "暂无法判断"),
        emotion_state=str(state.get("emotion_state") or "暂无法判断"),
        wait_pushback_count=int(state.get("wait_pushback") or 0),
        work_order_count=int(state.get("work_order") or 0),
        abnormal_end_count=int(state.get("abnormal_end") or 0),
        contact_unresolved_count=int(state.get("contact_unresolved") or 0),
        dissatisfaction_count=int(state.get("dissatisfaction") or 0),
    )
    return {
        "service_mode": mode.mode,
        "mode_id": mode.mode_id,
        "mode_components": [
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
            for component in mode.components
        ],
        "strategy_reason": mode.basis,
        "service_suggestion": mode.focus,
        "communication": mode.communication,
        "avoid": mode.avoid,
        "matched_facts": list(mode.matched_facts),
    }


def profile_snapshot(state: dict[str, object]) -> dict[str, object]:
    """Build the transparent three-dimension projection used by the showcase."""

    facts = []
    fact_keys = (
        ("wait_pushback", "等待推诿"),
        ("work_order", "历史工单"),
        ("abnormal_end", "异常中断"),
        ("contact_unresolved", "联系后未解决"),
        ("dissatisfaction", "对坐席不满"),
    )
    for key, label in fact_keys:
        if int(state.get(key) or 0):
            facts.append(label)
    items = [
        {
            "id": "proficiency",
            "name": "业务专业度",
            "value": str(state.get("proficiency_level") or "暂无法判断"),
            "values": [str(state.get("proficiency_level") or "暂无法判断")],
            "basis": str(state.get("proficiency_basis") or "可用证据不足。"),
        },
        {
            "id": "emotion",
            "name": "近期情绪状态",
            "value": str(state.get("emotion_state") or "暂无法判断"),
            "values": [str(state.get("emotion_state") or "暂无法判断")],
            "basis": str(state.get("emotion_basis") or "可用证据不足。"),
        },
        {
            "id": "facts",
            "name": "历史服务事实",
            "value": "、".join(facts) if facts else "近五个工作日未命中",
            "values": facts or ["近五个工作日未命中"],
            "basis": "历史事实按最近五个工作日的明确字段组合计算。",
        },
    ]
    mode = mode_payload(state)
    return {
        "items": items,
        "signature": " / ".join(str(item["value"]) for item in items),
        "active_category_count": sum(len(item["values"]) for item in items),
        "service_mode": mode["service_mode"],
        "service_actions": [],
    }

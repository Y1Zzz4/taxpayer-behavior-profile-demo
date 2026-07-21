"""Real-time, non-persistent phone-level service advice."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Protocol

from taxpayer_profile.llm_client import (
    REALTIME_ADVICE_PROMPT_VERSION,
    RealtimeServiceAdviceResult,
)
from taxpayer_profile.profiling import build_service_strategy
from taxpayer_profile.security import redact_sensitive_text


class AdviceClient(Protocol):
    model: str

    def generate_service_advice(
        self, payload: dict[str, object]
    ) -> RealtimeServiceAdviceResult: ...


_LIST_NUMBER_PREFIX = re.compile(
    r"^\s*(?:(?:\d+[.]\s*)+|\d+\s*[、．)）:：]\s*|[（(]\d+[）)]\s*)"
)


def _clean_list_number(value: str) -> str:
    """Remove model-supplied numbering before an HTML list adds its own marker."""

    cleaned = _LIST_NUMBER_PREFIX.sub("", value, count=1).strip()
    return cleaned or value.strip()


def _redact_context(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, list):
        return [_redact_context(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_context(item) for key, item in value.items()}
    return value


def _topics(context: dict[str, Any], *, unresolved_only: bool = False) -> list[str]:
    topics: list[str] = []
    for item in context.get("recent_trajectories", []):
        if not isinstance(item, dict):
            continue
        if unresolved_only and item.get("resolved") is not False:
            continue
        question = item.get("question") or item.get("question_category")
        if isinstance(question, str) and question and question not in topics:
            topics.append(question)
    return topics


def _failure_type(exc: Exception) -> str:
    current: BaseException = exc
    while current.__cause__ is not None:
        current = current.__cause__
    return type(current).__name__


def build_fallback_advice(
    context: dict[str, Any], *, fallback_reason: str
) -> dict[str, Any]:
    """Return fast deterministic advice when the interactive model is unavailable."""

    stats = context.get("statistics")
    stats = stats if isinstance(stats, dict) else {}
    recent_topics = _topics(context)
    unresolved_topics = _topics(context, unresolved_only=True)
    trajectories = context.get("recent_trajectories")
    trajectories = trajectories if isinstance(trajectories, list) else []
    has_pushback = any(
        isinstance(item, dict) and item.get("potential_pushback") is True
        for item in trajectories
    )
    latest_question = recent_topics[0] if recent_topics else None
    strategy = build_service_strategy(
        total_calls=int(stats.get("total_calls") or 0),
        repeated_issues=int(stats.get("repeated_issues") or 0),
        unresolved=int(stats.get("unresolved_calls") or 0),
        abnormal_ends=int(stats.get("abnormal_ends") or 0),
        dissatisfaction=int(stats.get("dissatisfaction_calls") or 0),
        has_pushback=has_pushback,
        latest_resolved=context.get("latest_resolved"),
        proficiency_score=context.get("proficiency_score"),
        work_orders=int(stats.get("work_orders") or 0),
        latest_question=latest_question,
        recent_questions=recent_topics,
        unresolved_questions=unresolved_topics,
    )

    followups: list[str] = []
    if unresolved_topics:
        followups.append(
            f"询问本次是否延续历史未解决事项“{unresolved_topics[0]}”，如不是则立即转入新诉求澄清。"
        )
    elif int(stats.get("work_orders") or 0) > 0:
        followups.append("询问本次是否与历史工单有关；只有来电人确认后再核验工单进度。")
    elif recent_topics:
        followups.append(
            f"可询问本次是否延续最近事项“{recent_topics[0]}”，不要直接假定是同一问题。"
        )

    risk_reminders: list[str] = []
    if int(stats.get("dissatisfaction_calls") or 0) > 0 or has_pushback:
        risk_reminders.append("历史存在服务风险信号，涉及等待或转交时主动说明原因、责任节点和预计时间。")
    if int(stats.get("abnormal_ends") or 0) > 0:
        risk_reminders.append("历史存在异常结束记录，结束前主动确认问题状态和后续安排。")

    proficiency = context.get("proficiency_score")
    if isinstance(proficiency, (int, float)) and proficiency >= 8:
        communication_style = "表达简洁专业，先给结论框架、适用条件和例外，再按需补充操作节点。"
    elif isinstance(proficiency, (int, float)) and proficiency <= 4:
        communication_style = "使用通俗表达，每次给出一至两个步骤，并在每个节点确认理解和操作结果。"
    else:
        communication_style = "先判断来电人对事项的熟悉程度，再控制术语密度和步骤数量。"

    if unresolved_topics:
        opening_strategy = (
            f"先开放式确认本次实际诉求，再询问是否延续历史未解决事项“{unresolved_topics[0]}”；"
            "只有来电人确认后才从历史节点继续。"
        )
    elif recent_topics:
        opening_strategy = (
            f"先确认本次实际诉求，可提示已看到最近事项“{recent_topics[0]}”；"
            "如来电人说明是新问题，应立即分开处理。"
        )
    else:
        opening_strategy = "先确认来电主体、本次办理环节和实际卡点，再决定解释深度与服务顺序。"

    sequence = [
        "确认来电主体及本次实际诉求，不预设本次问题与历史事项相同。",
    ]
    if followups:
        sequence.append(followups[0])
    sequence.extend(
        [
            "根据来电人的熟练度选择简洁结论式或分步引导式沟通。",
            "结束前确认本次问题状态、仍待处理节点和后续查询方式。",
        ]
    )

    evidence = [strategy.reason]
    if context.get("proficiency_summary"):
        evidence.append(f"历史熟练度依据：{context['proficiency_summary']}")

    history_facts = [f"历史来电{int(stats.get('total_calls') or 0)}次"]
    if int(stats.get("unresolved_calls") or 0) > 0:
        history_facts.append(f"有{int(stats['unresolved_calls'])}项未直接解决记录")
    if int(stats.get("work_orders") or 0) > 0:
        history_facts.append(f"有{int(stats['work_orders'])}项工单记录")
    if int(stats.get("repeated_issues") or 0) > 0:
        history_facts.append(f"有{int(stats['repeated_issues'])}次重复咨询")
    if unresolved_topics:
        priority_action = "建议先确认本次是否延续历史未解决事项，再从已确认的处理节点继续服务。"
    elif int(stats.get("work_orders") or 0) > 0:
        priority_action = "建议先确认本次是否涉及历史工单，确认后优先核对处理状态和反馈节点。"
    elif isinstance(proficiency, (int, float)) and proficiency >= 8:
        priority_action = "建议先确认本次诉求，再采用结论、适用条件和关键节点优先的沟通方式。"
    elif isinstance(proficiency, (int, float)) and proficiency <= 4:
        priority_action = "建议先确认本次诉求，再采用通俗、少步骤、逐节点确认的沟通方式。"
    else:
        priority_action = "建议先确认本次实际诉求，再根据来电人的理解程度调整解释深度。"
    advice_summary = "，".join(history_facts) + "；" + priority_action

    return {
        "generation_status": "rules_fallback",
        "fallback_reason": fallback_reason,
        "prompt_version": REALTIME_ADVICE_PROMPT_VERSION,
        "model_name": None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "advice_summary": advice_summary,
        "service_mode": strategy.recommended_mode,
        "opening_strategy": opening_strategy,
        "communication_style": communication_style,
        "history_followups": followups,
        "risk_reminders": risk_reminders,
        "avoid_actions": [
            "不要把历史问题直接当成本次来电问题。",
            "不要重复要求来电人完整复述数据库中已有且经其确认的历史信息。",
            "不要根据画像推断政策结论或替代本次业务核验。",
        ],
        "recommended_sequence": sequence,
        "evidence": evidence,
    }


def generate_realtime_advice(
    context: dict[str, Any], client: AdviceClient | None
) -> dict[str, Any]:
    """Generate advice without writing the result back to the profile database."""

    if client is None:
        return build_fallback_advice(context, fallback_reason="model_not_configured")
    try:
        result = client.generate_service_advice(
            {"history_profile": _redact_context(context)}
        )
    except Exception as exc:
        return build_fallback_advice(
            context, fallback_reason=f"model_{_failure_type(exc)}"
        )
    payload = result.model_dump()
    payload["recommended_sequence"] = [
        _clean_list_number(item) for item in result.recommended_sequence
    ]
    return {
        "generation_status": "model_generated",
        "fallback_reason": None,
        "prompt_version": REALTIME_ADVICE_PROMPT_VERSION,
        "model_name": client.model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }

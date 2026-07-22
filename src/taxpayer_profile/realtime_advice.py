"""Real-time, non-persistent phone-level service advice."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from taxpayer_profile.llm_client import (
    REALTIME_ADVICE_PROMPT_VERSION,
    RealtimeServiceAdviceResult,
)
from taxpayer_profile.profiling import classify_reception_mode
from taxpayer_profile.security import redact_sensitive_text


class AdviceClient(Protocol):
    model: str

    def generate_service_advice(
        self, payload: dict[str, object]
    ) -> RealtimeServiceAdviceResult: ...


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


def _mode_contract(context: dict[str, Any]) -> dict[str, Any]:
    """Build the authoritative mode contract from the shared rule catalog."""

    recent = context.get("recent_five_workdays")
    recent = recent if isinstance(recent, dict) else {}
    result = classify_reception_mode(
        proficiency_level=str(context.get("proficiency_level") or "暂无法判断"),
        emotion_state=str(context.get("emotion_state") or "暂无法判断"),
        wait_pushback_count=int(recent.get("wait_pushback_count") or 0),
        work_order_count=int(recent.get("work_order_count") or 0),
        abnormal_end_count=int(recent.get("abnormal_end_count") or 0),
        contact_unresolved_count=int(recent.get("contact_unresolved_count") or 0),
        dissatisfaction_count=int(recent.get("dissatisfaction_count") or 0),
    )
    components = [
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
    ]
    return {
        "service_mode": result.mode,
        "components": components,
        "selection_basis": result.basis,
        "required_focuses": [item["focus"] for item in components],
        "required_communications": [item["communication"] for item in components],
        "required_avoidances": [item["avoid"] for item in components],
    }


def _unique_texts(values: list[object], *, limit: int) -> list[str]:
    rendered: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in rendered:
            rendered.append(text[:300])
        if len(rendered) >= limit:
            break
    return rendered


def _combine_guidance(primary: str, generated: object, *, limit: int = 300) -> str:
    secondary = str(generated or "").strip()
    if not secondary or secondary in primary:
        return primary[:limit]
    if primary in secondary:
        return secondary[:limit]
    return f"{primary} {secondary}"[:limit]


def _anchor_model_result(
    context: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Keep generated wording subordinate to the deterministic mode contract."""

    contract = _mode_contract(context)
    mode = str(contract["service_mode"])
    components = contract["components"]
    summary = str(payload.get("advice_summary") or "").strip()
    if mode not in summary:
        summary = f"推荐采用“{mode}”：{summary}"
    payload["advice_summary"] = summary[:200]
    payload["service_mode"] = mode
    payload["service_modes"] = components
    component_summary = "；".join(
        f"{item['category']}采用“{item['mode']}”" for item in components
    )
    payload["mode_application"] = _combine_guidance(
        component_summary + "。",
        payload.get("mode_application"),
    )
    payload["service_focus"] = _unique_texts(
        [*contract["required_focuses"], *payload.get("service_focus", [])],
        limit=4,
    )
    payload["communication_style"] = _combine_guidance(
        "；".join(contract["required_communications"]),
        payload.get("communication_style"),
    )
    payload["avoid_actions"] = _unique_texts(
        [*contract["required_avoidances"], *payload.get("avoid_actions", [])],
        limit=5,
    )
    payload["evidence"] = _unique_texts(
        [
            *(item["basis"] for item in components),
            *payload.get("evidence", []),
        ],
        limit=6,
    )
    return payload


def build_fallback_advice(
    context: dict[str, Any], *, fallback_reason: str
) -> dict[str, Any]:
    """Return deterministic macro guidance when the interactive model is absent."""

    stats = context.get("statistics")
    stats = stats if isinstance(stats, dict) else {}
    five_days = context.get("recent_five_workdays")
    five_days = five_days if isinstance(five_days, dict) else {}
    contract = _mode_contract(context)
    mode = str(contract["service_mode"])
    components = contract["components"]
    unresolved_topics = _topics(context, unresolved_only=True)

    history_facts = [f"历史来电{int(stats.get('total_calls') or 0)}次"]
    if int(five_days.get("work_order_count") or 0):
        history_facts.append(f"近五个工作日有{int(five_days['work_order_count'])}次工单")
    if int(five_days.get("wait_pushback_count") or 0):
        history_facts.append(
            f"近五个工作日有{int(five_days['wait_pushback_count'])}次等待推诿信号"
        )
    if int(five_days.get("dissatisfaction_count") or 0):
        history_facts.append(
            f"近五个工作日有{int(five_days['dissatisfaction_count'])}次服务不满"
        )
    if int(five_days.get("abnormal_end_count") or 0):
        history_facts.append(
            f"近五个工作日有{int(five_days['abnormal_end_count'])}次异常中断"
        )
    if int(five_days.get("contact_unresolved_count") or 0):
        history_facts.append(
            "近五个工作日有"
            f"{int(five_days['contact_unresolved_count'])}次联系后未解决"
        )

    focuses = [str(value) for value in contract["required_focuses"]]
    communications = [str(value) for value in contract["required_communications"]]
    avoidances = [str(value) for value in contract["required_avoidances"]]
    communication = "；".join(communications)
    advice_summary = (
        "，".join(history_facts)
        + f"；建议采用“{mode}”，接通后先确认本次诉求，并同时落实三个分项方式。"
    )[:200]

    selected_modes = {str(item["category_id"]): str(item["mode"]) for item in components}
    if selected_modes["emotion_response"] == "安抚修复":
        opening = "先承接来电人的关注和既往服务体验，不争辩，并说明本通可处理范围。"
    elif selected_modes["emotion_response"] == "稳定预期":
        opening = "先确认其最关注的时限、结果或影响，明确当前可确认的范围。"
    else:
        opening = "自然确认本次来电主体、办理事项和当前卡点。"
    if selected_modes["matter_continuity"] == "历史跟进" and unresolved_topics:
        opening += f"再询问是否延续历史未解决事项“{unresolved_topics[0]}”，确认后从已有节点继续。"
    elif selected_modes["matter_continuity"] == "历史跟进":
        opening += "再确认是否延续历史事项，确认后核对最近处理节点。"
    else:
        opening += "历史记录仅作核对线索，不预设为本次诉求。"
    if selected_modes["information_delivery"] == "结论直述":
        opening += "确认诉求后优先回应关键判断和适用条件。"
    elif selected_modes["information_delivery"] == "重点解释":
        opening += "确认诉求后先说明关键判断，再解释必要条件。"
    else:
        opening += "确认诉求后使用通俗语言分段说明。"
    opening = opening[:300]

    followups: list[str] = []
    if unresolved_topics:
        followups.append(f"可核对历史未解决事项“{unresolved_topics[0]}”当前是否已有进展。")
    if int(five_days.get("work_order_count") or 0):
        followups.append("如本次涉及历史工单，重点核验当前状态、承办节点和反馈方式。")

    risk_reminders: list[str] = []
    if int(five_days.get("wait_pushback_count") or 0):
        risk_reminders.append("涉及等待或转交时，主动说明原因、责任节点和预计时间。")
    if int(five_days.get("abnormal_end_count") or 0):
        risk_reminders.append("历史存在异常中断，结束前应确认问题状态和后续安排。")
    if context.get("emotion_state") == "焦虑":
        risk_reminders.append("近期表达偏焦虑，说明结论时同步确认时限、结果和可能影响。")

    evidence = [str(context.get("mode_basis") or "当前由本地画像规则确定接待模式。")]
    if context.get("proficiency_basis"):
        evidence.append(f"业务熟悉度依据：{context['proficiency_basis']}")
    if context.get("emotion_basis"):
        evidence.append(f"近期情绪依据：{context['emotion_basis']}")

    return {
        "generation_status": "rules_fallback",
        "fallback_reason": fallback_reason,
        "prompt_version": REALTIME_ADVICE_PROMPT_VERSION,
        "model_name": None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "advice_summary": advice_summary,
        "service_mode": mode,
        "service_modes": components,
        "mode_application": (
            "；".join(
                f"{item['category']}采用“{item['mode']}”：{item['focus']}"
                for item in components
            )
            + "。"
        )[:300],
        "service_focus": focuses,
        "opening_strategy": opening,
        "communication_style": communication,
        "history_followups": followups,
        "risk_reminders": risk_reminders,
        "avoid_actions": _unique_texts(
            [*avoidances, "不要根据历史画像推断本次业务结论。"], limit=5
        ),
        "evidence": _unique_texts(
            [*(item["basis"] for item in components), *evidence], limit=6
        ),
    }


def generate_realtime_advice(
    context: dict[str, Any], client: AdviceClient | None
) -> dict[str, Any]:
    """Generate advice without writing the result back to the profile database."""

    if client is None:
        return build_fallback_advice(context, fallback_reason="model_not_configured")
    try:
        model_input = {
            "required_mode_contract": _mode_contract(context),
            "history_profile": context,
        }
        result = client.generate_service_advice(_redact_context(model_input))
    except Exception as exc:
        return build_fallback_advice(
            context, fallback_reason=f"model_{_failure_type(exc)}"
        )
    payload = result.model_dump()
    payload.pop("recommended_sequence", None)
    payload = _anchor_model_result(context, payload)
    return {
        "generation_status": "model_generated",
        "fallback_reason": None,
        "prompt_version": REALTIME_ADVICE_PROMPT_VERSION,
        "model_name": client.model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }

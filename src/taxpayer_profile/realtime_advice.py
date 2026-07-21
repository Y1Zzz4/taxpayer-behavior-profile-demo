"""Real-time, non-persistent phone-level service advice."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from taxpayer_profile.llm_client import (
    REALTIME_ADVICE_PROMPT_VERSION,
    RealtimeServiceAdviceResult,
)
from taxpayer_profile.profiling import RECEPTION_MODE_CATALOG
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


def _mode_contract(context: dict[str, Any]) -> dict[str, str]:
    """Build the authoritative mode contract from the shared rule catalog."""

    requested_mode = str(context.get("recommended_mode") or "通俗引导")
    catalog_mode = next(
        (
            item
            for item in RECEPTION_MODE_CATALOG
            if str(item["label"]) == requested_mode
        ),
        RECEPTION_MODE_CATALOG[-1],
    )
    guidance = context.get("mode_guidance")
    guidance = guidance if isinstance(guidance, dict) else {}
    return {
        "service_mode": str(catalog_mode["label"]),
        "selection_basis": str(
            context.get("mode_basis") or "由本地画像规则确定接待模式。"
        ),
        "required_focus": str(guidance.get("focus") or catalog_mode["focus"]),
        "required_communication": str(
            guidance.get("communication") or catalog_mode["communication"]
        ),
        "required_avoid": str(guidance.get("avoid") or catalog_mode["avoid"]),
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
    mode = contract["service_mode"]
    summary = str(payload.get("advice_summary") or "").strip()
    if mode not in summary:
        summary = f"推荐采用“{mode}”：{summary}"
    payload["advice_summary"] = summary[:200]
    payload["service_mode"] = mode
    payload["mode_application"] = _combine_guidance(
        f"采用“{mode}”：{contract['required_focus']}",
        payload.get("mode_application"),
    )
    payload["service_focus"] = _unique_texts(
        [contract["required_focus"], *payload.get("service_focus", [])],
        limit=4,
    )
    payload["communication_style"] = _combine_guidance(
        contract["required_communication"], payload.get("communication_style")
    )
    payload["avoid_actions"] = _unique_texts(
        [contract["required_avoid"], *payload.get("avoid_actions", [])],
        limit=5,
    )
    payload["evidence"] = _unique_texts(
        [contract["selection_basis"], *payload.get("evidence", [])],
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
    mode = contract["service_mode"]
    recent_topics = _topics(context)
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

    focus = contract["required_focus"]
    communication = contract["required_communication"]
    avoid = contract["required_avoid"]
    advice_summary = (
        "，".join(history_facts)
        + f"；推荐采用“{mode}”：{focus}"
    )

    if mode == "耐心安抚":
        opening = "先自然确认本次诉求，同时留意是否需要承接既往服务体验；确认后再说明本通可处理范围。"
    elif mode == "问题跟进" and unresolved_topics:
        opening = (
            f"自然确认本次诉求后，可询问是否延续历史未解决事项“{unresolved_topics[0]}”；"
            "只有来电人确认后再从历史节点继续。"
        )
    elif mode == "问题跟进":
        opening = "先确认本次是否延续历史事项；确认后核对最近处理状态和待补信息。"
    elif mode == "结论直给":
        opening = "先确认本次核心诉求和适用场景，再优先回应关键判断与适用条件。"
    elif recent_topics:
        opening = (
            f"先确认本次实际诉求；必要时提示已看到最近咨询“{recent_topics[0]}”，"
            "但不要预设为同一事项。"
        )
    else:
        opening = "先确认来电主体、本次办理事项和当前卡点，再选择解释深度。"

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
        "mode_application": f"采用“{mode}”：{focus}沟通时应做到：{communication}",
        "service_focus": [focus],
        "opening_strategy": opening,
        "communication_style": communication,
        "history_followups": followups,
        "risk_reminders": risk_reminders,
        "avoid_actions": [avoid, "不要根据历史画像推断本次业务结论。"],
        "evidence": _unique_texts(
            [contract["selection_basis"], *evidence], limit=6
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

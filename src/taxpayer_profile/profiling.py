"""Phone-level aggregation and neutral service strategy rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def weighted_proficiency(values: list[tuple[datetime, float | None]]) -> float | None:
    """Use linearly increasing weights 1..n after chronological sorting."""

    scored = sorted(
        ((call_time, score) for call_time, score in values if score is not None),
        key=lambda item: item[0],
    )
    if not scored:
        return None
    weighted_sum = sum(score * index for index, (_, score) in enumerate(scored, 1))
    weight_total = sum(range(1, len(scored) + 1))
    return round(weighted_sum / weight_total, 2)


@dataclass(frozen=True)
class ProficiencyResult:
    score: float | None
    summary: str


def analyze_proficiency(
    *,
    transcript: str | None,
    business_content: str | None,
    core_question: str | None,
    effective_qa_content: str | None,
    effective_qa_turns: int | None,
    service_was_unclear: bool,
) -> ProficiencyResult:
    """Estimate proficiency from concrete evidence, returning unknown if sparse."""

    combined = " ".join(
        value
        for value in (transcript, business_content, core_question, effective_qa_content)
        if value
    )
    if len(combined) < 30:
        return ProficiencyResult(None, "无法判断")

    score = 5
    evidence: list[str] = []
    if core_question and len(core_question) >= 8:
        score += 1
        evidence.append("能够说明实际问题")
    if any(
        term in combined
        for term in ("申报", "发票", "税费", "扣缴", "增值税", "所得税", "电子税务局")
    ):
        score += 1
        evidence.append("能够使用相关业务术语")
    if any(
        term in combined
        for term in ("系统提示", "模块", "菜单", "页面", "路径", "步骤", "报错")
    ):
        score += 1
        evidence.append("能够描述系统或办理环节")
    if effective_qa_turns is not None and effective_qa_turns >= 2 and effective_qa_content:
        score += 1
        evidence.append("能够继续提出有效问题")
    if not service_was_unclear and any(
        term in combined for term in ("不知道怎么办", "完全不懂", "不会操作", "听不明白")
    ):
        score -= 1
        evidence.append("需要更细化的操作说明")

    bounded = float(max(1, min(10, score)))
    if not evidence:
        return ProficiencyResult(bounded, "信息有限，建议结合后续来电继续观察。")
    return ProficiencyResult(bounded, "；".join(evidence[:2]) + "。")


@dataclass(frozen=True)
class ServiceResult:
    rating: str
    summary: str


def analyze_service(
    *,
    resolved: bool | None,
    waiting: bool | None,
    pushback: bool | None,
    dissatisfied: bool | None,
    has_answer: bool,
) -> ServiceResult:
    """Assess service process only; never claim policy correctness."""

    if not has_answer and resolved is None and not any((waiting, pushback, dissatisfied)):
        return ServiceResult("无法判断", "无法判断：可用答复信息不足。")
    if pushback or dissatisfied:
        details = []
        if pushback:
            details.append("存在潜在推诿信号")
        if dissatisfied:
            details.append("纳税人表达不满")
        return ServiceResult("需关注", f"需关注：{'，'.join(details)}。")
    if resolved is False or waiting:
        details = []
        if waiting:
            details.append("出现等待表述")
        if resolved is False:
            details.append("未直接解决当前问题")
        return ServiceResult("一般", f"一般：{'，'.join(details)}。")
    if resolved is True and has_answer:
        return ServiceResult("良好", "良好：文本显示给出了相关答复并直接解决问题。")
    return ServiceResult("一般", "一般：给出了答复信息，但解决情况无法确认。")


@dataclass(frozen=True)
class ServiceProfileClassification:
    """Transparent phone-level segment for service preparation, not tax risk."""

    profile_type: str
    basis: str


def classify_service_profile(
    *,
    total_calls: int,
    repeated_issues: int,
    unresolved: int,
    work_orders: int,
    dissatisfaction: int,
    proficiency_score: float | None,
) -> ServiceProfileClassification:
    """Assign one primary service profile; capability remains an independent dimension."""

    if dissatisfaction > 0:
        return ServiceProfileClassification(
            "服务关注型",
            f"历史出现{dissatisfaction}次对本通热线服务不满记录，接待时应提高节点透明度。",
        )
    if unresolved > 0 or work_orders > 0:
        signals = []
        if unresolved:
            signals.append(f"{unresolved}次未直接解决")
        if work_orders:
            signals.append(f"{work_orders}次工单")
        return ServiceProfileClassification(
            "事项跟进型",
            f"历史存在{'、'.join(signals)}，需要优先确认事项当前处理节点。",
        )
    if repeated_issues > 0 or total_calls >= 3:
        return ServiceProfileClassification(
            "持续咨询型",
            f"累计来电{total_calls}次、同类诉求{repeated_issues}次，已形成持续咨询特征。",
        )
    if total_calls <= 1:
        return ServiceProfileClassification(
            "常规服务型",
            "当前仅有一次历史来电，未出现优先关注信号；具体沟通方式由业务认知维度决定。",
        )
    capability_basis = (
        f"历史业务熟练度为{proficiency_score:.1f}/10，将作为接待模式的判定依据。"
        if proficiency_score is not None
        else "业务熟练度证据不足，接待中需动态确认理解程度。"
    )
    return ServiceProfileClassification(
        "常规服务型",
        f"累计来电{total_calls}次，暂未出现关注、跟进或持续咨询信号；{capability_basis}",
    )


@dataclass(frozen=True)
class ServiceStrategy:
    attention_level: str
    recommended_mode: str
    reason: str
    suggestion: str


def build_service_strategy(
    *,
    total_calls: int,
    repeated_issues: int,
    unresolved: int,
    abnormal_ends: int,
    dissatisfaction: int,
    has_pushback: bool,
    latest_resolved: bool | None,
    proficiency_score: float | None,
    work_orders: int = 0,
    latest_question: str | None = None,
    recent_questions: list[str] | None = None,
    unresolved_questions: list[str] | None = None,
) -> ServiceStrategy:
    topic = latest_question or "最近咨询事项"
    unresolved_topic = (unresolved_questions or [topic])[0]
    recent_topic = (recent_questions or [topic])[0]
    risk_signals: list[str] = []
    if dissatisfaction:
        risk_signals.append(f"历史出现{dissatisfaction}次本通不满")
    if abnormal_ends >= 2:
        risk_signals.append(f"历史出现{abnormal_ends}次异常结束")
    if has_pushback:
        risk_signals.append("历史出现潜在推诿信号")
    if risk_signals:
        return ServiceStrategy(
            attention_level="服务风险",
            recommended_mode="信任修复与节点透明型",
            reason="；".join(risk_signals) + "。",
            suggestion=(
                f"先复述并确认对“{topic}”的理解；涉及等待或转交时主动说明原因、"
                "预计时间和下一责任方；给出方案后确认来电人是否接受，避免直接结束或重复转接。"
            ),
        )
    if unresolved >= 3 and repeated_issues >= 2:
        return ServiceStrategy(
            attention_level="重点关注",
            recommended_mode="事项进度核验与闭环型",
            reason=(
                f"累计来电{total_calls}次，其中{unresolved}次未直接解决、"
                f"{repeated_issues}次确认为重复问题。"
            ),
            suggestion=(
                f"接通后先核对历史未解决事项“{unresolved_topic}”的当前进度，"
                "不要求来电人从头复述；明确本通可完成内容、后续责任对象和时间点，"
                "结束前逐项确认仍待处理事项。"
            ),
        )
    is_work_order_topic = "工单" in topic
    if work_orders > 0 or is_work_order_topic:
        work_order_reason = (
            f"历史共有{work_orders}次工单记录，最近事项为“{topic}”。"
            if work_orders > 0
            else f"最近事项“{topic}”明确属于工单查询。"
        )
        return ServiceStrategy(
            attention_level="待跟进",
            recommended_mode="事项进度核验与闭环型",
            reason=work_order_reason,
            suggestion=(
                f"优先核验“{topic}”对应工单的受理时间、当前状态和承办节点，"
                "先回答进度再补充业务口径；如仍需等待，明确预计反馈时间和查询方式，避免重复登记。"
            ),
        )
    if unresolved >= 1 or latest_resolved is False:
        return ServiceStrategy(
            attention_level="多次未解决" if unresolved >= 2 else "待跟进",
            recommended_mode="事项进度核验与闭环型",
            reason=f"累计{unresolved}次未直接解决，最近事项为“{unresolved_topic}”。",
            suggestion=(
                f"优先询问历史事项“{unresolved_topic}”目前停在哪个处理节点，"
                "避免重复提供相同口径；随后给出本次可执行动作、所需材料和明确的后续时间点。"
            ),
        )
    if repeated_issues > 0:
        return ServiceStrategy(
            attention_level="重复咨询",
            recommended_mode="重复问题差异核对型",
            reason=f"历史已确认{repeated_issues}次重复问题，最近相关事项为“{recent_topic}”。",
            suggestion=(
                f"开场说明已看到“{recent_topic}”的历史记录，先确认本次新增变化是进度、"
                "材料、系统提示还是答复理解问题；只解释差异和下一步，并确认前次问题是否已经关闭。"
            ),
        )
    if proficiency_score is not None and proficiency_score >= 8:
        return ServiceStrategy(
            attention_level="普通",
            recommended_mode="结论条件优先型",
            reason=f"历史办税熟练度为{proficiency_score:.1f}/10，能够理解业务术语和办理环节。",
            suggestion=(
                f"围绕“{topic}”先给结论、适用条件和例外，再列关键办理节点；"
                "默认来电人熟悉基础概念，除非对方追问，不展开通用入门步骤。"
            ),
        )
    if proficiency_score is not None and proficiency_score <= 4:
        return ServiceStrategy(
            attention_level="普通",
            recommended_mode="分步操作陪伴确认型",
            reason=f"历史办税熟练度为{proficiency_score:.1f}/10，需要更具体的操作引导。",
            suggestion=(
                f"围绕“{topic}”先用通俗语言说明目标，再一次给出1—2个操作步骤；"
                "每完成一个页面或节点后再继续，并请来电人复述当前结果，避免连续堆叠术语。"
            ),
        )
    if total_calls > 1:
        return ServiceStrategy(
            attention_level="重复来电",
            recommended_mode="历史上下文衔接型",
            reason=f"该号码已有{total_calls}次来电，最近咨询为“{recent_topic}”。",
            suggestion=(
                f"接通后先确认本次是否延续“{recent_topic}”；若是延续事项，直接从上次节点继续，"
                "若是新问题则明确分开记录，避免把不同事项混为重复咨询。"
            ),
        )
    return ServiceStrategy(
        attention_level="普通",
        recommended_mode="首次诉求澄清与标准引导型",
        reason="历史来电较少或熟练度证据不足，暂无明确风险和稳定沟通偏好。",
        suggestion=(
            f"先确认“{topic}”对应的主体、办理环节和当前卡点，再给出结论及不超过3个关键步骤；"
            "结束前询问问题是否已解决，并说明仍需后续处理的节点。"
        ),
    )


def attention_and_service_strategy(
    **kwargs: object,
) -> tuple[str, str, str]:
    """Backward-compatible tuple interface used by external callers and tests."""

    strategy = build_service_strategy(**kwargs)  # type: ignore[arg-type]
    return (
        strategy.attention_level,
        strategy.recommended_mode,
        strategy.suggestion,
    )

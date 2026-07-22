"""Phone-level aggregation and neutral service strategy rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

PROFICIENCY_LEVELS = ("专业", "了解", "小白")
EMOTION_STATES = ("平稳", "焦虑", "不满")

RECEPTION_MODE_GROUPS = (
    {
        "id": "emotion_response",
        "label": "情绪响应",
        "description": "根据近期情绪和历史服务体验确定沟通基调。",
        "color": "#8B5FC0",
        "modes": (
            {
                "id": "repair",
                "label": "安抚修复",
                "rule": "近期情绪为不满，或近五个工作日出现等待推诿、对坐席不满",
                "focus": "先承接情绪和历史服务体验，再说明本通可处理范围、责任节点与反馈方式。",
                "communication": "语气稳定克制，先复述确认，不争辩；涉及等待或转交时主动说明原因和节点。",
                "avoid": "避免机械重复历史口径、直接转接或在未说明原因时让来电人继续等待。",
            },
            {
                "id": "stabilize",
                "label": "稳定预期",
                "rule": "未命中安抚修复，且近期情绪为焦虑",
                "focus": "围绕来电人关注的时限、结果和可能影响，明确已知信息与待确认边界。",
                "communication": "节奏稳定，先回应最担心的问题，再说明可确认内容、时间预期和后续节点。",
                "avoid": "避免使用模糊承诺、忽略时限焦虑，或一次补充过多非关键背景。",
            },
            {
                "id": "steady",
                "label": "平稳接待",
                "rule": "未命中安抚修复或稳定预期",
                "focus": "保持清晰、自然的沟通节奏，根据本次诉求正常组织服务。",
                "communication": "语气客观友好，确认关键信息后进入事项处理，并根据反馈调整节奏。",
                "avoid": "避免无依据放大历史风险或预设来电人存在负面情绪。",
            },
        ),
    },
    {
        "id": "matter_continuity",
        "label": "业务应对",
        "description": "根据近期历史事项确定从当前诉求开始还是衔接既有节点。",
        "color": "#C27A2C",
        "modes": (
            {
                "id": "followup",
                "label": "历史诉求跟进",
                "rule": "近五个工作日存在历史工单、异常中断或联系后未解决",
                "focus": "先确认本次是否延续历史事项；确认后核验当前状态、承办节点和待补信息。",
                "communication": "从已确认的历史节点继续，减少重复复述，并明确本通能够推进的范围。",
                "avoid": "避免重复登记、重复提供已被证明无效的口径，或把历史问题直接认定为本次诉求。",
            },
            {
                "id": "clarify",
                "label": "当前诉求确认",
                "rule": "近五个工作日未出现需要优先衔接的事项事实",
                "focus": "先确认本次来电的主体、事项和当前卡点，再决定是否调用历史信息辅助服务。",
                "communication": "以本次表达为主，历史信息仅作为核对线索，不预设来电目的。",
                "avoid": "避免因历史记录相似而跳过本次诉求确认。",
            },
        ),
    },
    {
        "id": "information_delivery",
        "label": "表达方式",
        "description": "根据业务专业度确定信息密度、术语深度和说明顺序。",
        "color": "#327FA8",
        "modes": (
            {
                "id": "direct",
                "label": "结论直述",
                "rule": "业务专业度为专业",
                "focus": "先给关键结论、适用条件和必要办理节点，再根据追问补充边界。",
                "communication": "表达简洁准确，保留必要专业术语，减少基础概念铺垫。",
                "avoid": "避免连续展开通用知识，或只讲流程而不先说明结论。",
            },
            {
                "id": "explain",
                "label": "重点解释",
                "rule": "业务专业度为了解",
                "focus": "先说明关键判断，再补充必要条件、原因和容易混淆的节点。",
                "communication": "使用适量业务术语，每次围绕一个重点解释，并通过追问确认理解。",
                "avoid": "避免过度简化关键条件，也避免一次性展开过多规则细节。",
            },
            {
                "id": "guide",
                "label": "通俗引导",
                "rule": "业务专业度为小白或暂无法判断",
                "focus": "先用通俗语言说明目标和判断结果，再按少量关键节点分段引导。",
                "communication": "降低术语密度，一次说明一至两个关键节点，并根据反馈调整解释深度。",
                "avoid": "避免堆叠专业术语、一次给出过多操作，或在未确认理解时快速结束。",
            },
        ),
    },
)

# All external presentations use the same combination order: expression,
# emotion, then business response. Category ids remain stable for compatibility.
_GROUP_ORDER = {
    "information_delivery": 0,
    "emotion_response": 1,
    "matter_continuity": 2,
}
RECEPTION_MODE_GROUPS = tuple(
    sorted(RECEPTION_MODE_GROUPS, key=lambda item: _GROUP_ORDER[str(item["id"])])
)

RECEPTION_MODE_CATALOG = tuple(
    {
        **mode,
        "category_id": group["id"],
        "category": group["label"],
        "color": group["color"],
    }
    for group in RECEPTION_MODE_GROUPS
    for mode in group["modes"]
)


def proficiency_level_from_score(score: float | None) -> str:
    """Convert the legacy numeric score into the approved three-level label."""

    if score is None:
        return "暂无法判断"
    if score >= 8:
        return "专业"
    if score >= 5:
        return "了解"
    return "小白"


def normalize_proficiency_level(value: str | None, score: float | None = None) -> str:
    rendered = (value or "").strip().removesuffix("型")
    aliases = {
        "熟悉": "了解",
        "一般": "了解",
        "新手": "小白",
        "不熟悉": "小白",
        "无法判断": "暂无法判断",
        "证据不足": "暂无法判断",
    }
    rendered = aliases.get(rendered, rendered)
    if rendered in {*PROFICIENCY_LEVELS, "暂无法判断"}:
        return rendered
    return proficiency_level_from_score(score)


def infer_emotion_state(
    *, transcript: str | None, business_content: str | None, answer_content: str | None
) -> tuple[str, str]:
    """Conservative rules-only fallback; a configured model remains preferred."""

    combined = " ".join(
        value for value in (business_content, answer_content, transcript) if value
    )
    if len(combined) < 12:
        return "暂无法判断", "可用表达不足，暂不预设来电人情绪。"
    dissatisfied_terms = (
        "投诉",
        "不满意",
        "态度不好",
        "推诿",
        "怎么又",
        "一直不给",
        "根本没",
        "太差",
    )
    anxious_terms = (
        "着急",
        "急着",
        "怎么办",
        "来不及",
        "马上到期",
        "什么时候",
        "还要多久",
        "会不会罚",
        "影响",
    )
    if any(term in combined for term in dissatisfied_terms):
        return "不满", "表达中出现明确负面评价、责备或投诉倾向。"
    if any(term in combined for term in anxious_terms):
        return "焦虑", "表达中持续关注时限、结果或可能产生的影响。"
    return "平稳", "表达整体有序，未出现明显担忧、责备或负面评价。"


@dataclass(frozen=True)
class ReceptionModeComponent:
    category_id: str
    category: str
    mode_id: str
    mode: str
    basis: str
    focus: str
    communication: str
    avoid: str


@dataclass(frozen=True)
class ReceptionModeResult:
    mode_id: str
    mode: str
    basis: str
    focus: str
    communication: str
    avoid: str
    matched_facts: tuple[str, ...]
    components: tuple[ReceptionModeComponent, ...]


def classify_reception_mode(
    *,
    proficiency_level: str | None,
    emotion_state: str | None,
    wait_pushback_count: int = 0,
    work_order_count: int = 0,
    abnormal_end_count: int = 0,
    contact_unresolved_count: int = 0,
    dissatisfaction_count: int = 0,
) -> ReceptionModeResult:
    """Select one mode from each category and compose a service strategy."""

    level = normalize_proficiency_level(proficiency_level)
    emotion = (emotion_state or "暂无法判断").strip().removesuffix("型")
    if emotion not in {*EMOTION_STATES, "暂无法判断"}:
        emotion = "暂无法判断"

    facts: list[str] = []
    if wait_pushback_count:
        facts.append(f"等待且潜在推诿{wait_pushback_count}次")
    if work_order_count:
        facts.append(f"历史工单{work_order_count}次")
    if abnormal_end_count:
        facts.append(f"异常中断{abnormal_end_count}次")
    if contact_unresolved_count:
        facts.append(f"联系后未解决{contact_unresolved_count}次")
    if dissatisfaction_count:
        facts.append(f"对坐席不满{dissatisfaction_count}次")

    catalog = {str(item["id"]): item for item in RECEPTION_MODE_CATALOG}
    if emotion == "不满" or wait_pushback_count or dissatisfaction_count:
        emotion_mode = catalog["repair"]
        emotion_basis = (
            f"近期情绪为{emotion}；"
            + "、".join(
                fact
                for fact in facts
                if fact.startswith(("等待且潜在推诿", "对坐席不满"))
            )
        ).rstrip("；")
    elif emotion == "焦虑":
        emotion_mode = catalog["stabilize"]
        emotion_basis = "近期情绪为焦虑，需要同步稳定时限、结果和影响预期。"
    else:
        emotion_mode = catalog["steady"]
        emotion_basis = f"近期情绪为{emotion}，且未出现需要优先修复的服务体验信号。"

    continuity_facts = [
        fact
        for fact in facts
        if fact.startswith(("历史工单", "异常中断", "联系后未解决"))
    ]
    if continuity_facts:
        continuity_mode = catalog["followup"]
        continuity_basis = (
            "近五个工作日存在" + "、".join(continuity_facts) + "，需要先确认是否延续并核对进展。"
        )
    else:
        continuity_mode = catalog["clarify"]
        continuity_basis = "近五个工作日未出现需要优先衔接的事项事实，应先确认本次实际诉求。"

    expression_id = {
        "专业": "direct",
        "了解": "explain",
        "小白": "guide",
        "暂无法判断": "guide",
    }[level]
    expression_mode = catalog[expression_id]
    expression_basis = f"业务专业度为{level}，采用{expression_mode['label']}。"

    groups = {str(group["id"]): group for group in RECEPTION_MODE_GROUPS}
    selected_modes = (
        (groups["information_delivery"], expression_mode, expression_basis),
        (groups["emotion_response"], emotion_mode, emotion_basis),
        (groups["matter_continuity"], continuity_mode, continuity_basis),
    )
    components = tuple(
        ReceptionModeComponent(
            category_id=str(group["id"]),
            category=str(group["label"]),
            mode_id=str(selected["id"]),
            mode=str(selected["label"]),
            basis=component_basis,
            focus=str(selected["focus"]),
            communication=str(selected["communication"]),
            avoid=str(selected["avoid"]),
        )
        for group, selected, component_basis in selected_modes
    )
    combined_mode = " · ".join(component.mode for component in components)
    combined_basis = "；".join(
        f"{component.category}：{component.basis}" for component in components
    )
    return ReceptionModeResult(
        mode_id="+".join(component.mode_id for component in components),
        mode=combined_mode,
        basis=combined_basis,
        focus="；".join(component.focus for component in components),
        communication="；".join(component.communication for component in components),
        avoid="；".join(component.avoid for component in components),
        matched_facts=tuple(facts),
        components=components,
    )


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
            f"累计来电{total_calls}次、重复诉求{repeated_issues}次，已形成持续咨询特征。",
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

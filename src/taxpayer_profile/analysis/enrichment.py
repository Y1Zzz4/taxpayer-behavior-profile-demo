"""Enrich one normalized call and merge trusted or model-derived fields."""

from __future__ import annotations

from dataclasses import replace

from taxpayer_profile.analysis.contracts import (
    AnalysisClient,
    EnrichmentMetadata,
    ModelExtraction,
)
from taxpayer_profile.identity import resolve_enterprise_identity
from taxpayer_profile.ingestion.modes import InputMode
from taxpayer_profile.llm_client import (
    CallExtractionResult,
    HistoryEnrichmentResult,
    build_call_payload,
)
from taxpayer_profile.normalization import NormalizedCallInput
from taxpayer_profile.profiling import (
    analyze_proficiency,
    analyze_service,
    infer_emotion_state,
    normalize_proficiency_level,
)


def _local_trusted_enrichment(call: NormalizedCallInput) -> NormalizedCallInput:
    """Fill gaps in a legacy trusted record without inventing source facts."""

    service = analyze_service(
        resolved=call.resolved_status,
        waiting=call.waiting_expression,
        pushback=call.potential_pushback,
        dissatisfied=call.taxpayer_dissatisfied,
        has_answer=bool(call.answer_content),
    )
    proficiency = analyze_proficiency(
        transcript=call.transcript,
        business_content=call.business_content,
        core_question=call.core_question,
        effective_qa_content=call.effective_qa_content,
        effective_qa_turns=call.effective_qa_turns,
        service_was_unclear=bool(call.potential_pushback),
    )
    proficiency_level = normalize_proficiency_level(
        (
            call.proficiency_level
            if call.proficiency_level not in {"", "无法判断", "暂无法判断"}
            else None
        ),
        proficiency.score,
    )
    proficiency_basis = (
        call.proficiency_basis
        if call.proficiency_level not in {None, "暂无法判断", "无法判断"}
        else proficiency.summary
    )
    emotion_state, emotion_basis = infer_emotion_state(
        transcript=call.transcript,
        business_content=call.business_content,
        answer_content=call.answer_content,
    )
    combined = " ".join(
        value
        for value in (call.core_question, call.business_content, call.answer_content)
        if value
    )
    if call.work_order:
        demand_category = "工单/拉起类"
    elif any(term in combined for term in ("进度", "查询", "联系方式", "地址")):
        demand_category = "涉税查询类"
    elif any(term in combined for term in ("报错", "异常", "无法登录", "页面空白")):
        demand_category = "系统异常类"
    elif any(term in combined for term in ("投诉", "举报")):
        demand_category = "投诉举报类"
    elif any(term in combined for term in ("如何", "操作", "办理", "材料", "申报")):
        demand_category = "操作辅导类"
    else:
        demand_category = "其他类"
    unresolved_reason = None
    if call.resolved_status is False:
        if call.work_order:
            unresolved_reason = "已转工单或内部流转，需等待后续处理"
        elif call.contacted_other_department:
            unresolved_reason = "需联系相关人员或部门继续办理"
        else:
            unresolved_reason = "本通记录显示问题未直接解决，具体原因未明确"
    return replace(
        call,
        demand_category=call.demand_category or demand_category,
        unresolved_reason=unresolved_reason,
        proficiency_score=proficiency.score,
        proficiency_summary=proficiency.summary,
        proficiency_level=proficiency_level,
        proficiency_basis=proficiency_basis,
        emotion_state=(
            call.emotion_state
            if call.emotion_state in {"平稳", "焦虑", "不满"}
            else emotion_state
        ),
        emotion_basis=(
            call.emotion_basis
            if call.emotion_state in {"平稳", "焦虑", "不满"}
            else emotion_basis
        ),
        service_rating=service.rating,
        service_summary=service.summary,
    )


def _merge_incremental_extraction(
    call: NormalizedCallInput,
    extraction: CallExtractionResult,
) -> NormalizedCallInput:
    """Merge a fresh extraction while preserving only confirmed reusable fields."""

    caller_type = (
        None if extraction.caller_type == "无法判断" else extraction.caller_type
    )
    identity = resolve_enterprise_identity(
        caller_type=caller_type,
        raw_identity=call.raw_identity_label,
        explicit_identity=extraction.explicit_enterprise_identity,
    )
    unresolved_reason = None
    if extraction.resolved_status is False:
        unresolved_reason = extraction.unresolved_reason
        if not unresolved_reason:
            if call.work_order:
                unresolved_reason = "已转工单或内部流转，需等待后续处理"
            elif extraction.contacted_other_department:
                unresolved_reason = "需联系相关人员或部门继续办理"
            else:
                unresolved_reason = "本通未形成明确处理路径"

    return replace(
        call,
        # Core question is an approved reusable field. The model fills it only
        # when the incremental source left it empty.
        core_question=call.core_question or extraction.core_question,
        agent_answer_summary=extraction.agent_answer_summary,
        demand_category=", ".join(extraction.demand_categories),
        father_question=extraction.father_question,
        father_question_2=extraction.father_question_2,
        caller_type=caller_type,
        enterprise_identity=identity.identity,
        resolved_status=extraction.resolved_status,
        unresolved_reason=unresolved_reason,
        model_abnormal_end=extraction.model_abnormal_end,
        waiting_expression=extraction.waiting_expression,
        potential_pushback=extraction.potential_pushback,
        taxpayer_dissatisfied=extraction.taxpayer_dissatisfied,
        contacted_other_department=extraction.contacted_other_department,
        active_contacted_other_department=extraction.active_contacted_other_department,
        contact_target=extraction.contact_target,
        natural_qa_turns=extraction.natural_qa_turns,
        core_question_turns=extraction.core_question_turns,
        effective_qa_turns=extraction.effective_qa_turns,
        effective_qa_content=extraction.effective_qa_content,
        proficiency_score=extraction.proficiency_score,
        proficiency_summary=extraction.proficiency_summary,
        proficiency_level=extraction.proficiency_level,
        proficiency_basis=extraction.proficiency_basis,
        emotion_state=extraction.emotion_state,
        emotion_basis=extraction.emotion_basis,
        service_rating=extraction.service_rating,
        service_summary=extraction.service_summary,
        enterprise_identity_source=identity.source,
        enterprise_identity_conflict=identity.conflict,
    )


def _merge_history_enrichment(
    call: NormalizedCallInput,
    extraction: HistoryEnrichmentResult,
) -> NormalizedCallInput:
    """Preserve legacy trusted decisions and use the model only for gaps."""

    caller_type = call.caller_type or (
        None if extraction.caller_type == "无法判断" else extraction.caller_type
    )
    identity = resolve_enterprise_identity(
        caller_type=caller_type,
        raw_identity=call.raw_identity_label,
        explicit_identity=extraction.explicit_enterprise_identity,
    )

    def existing_label_or(extracted: str, existing: str) -> str:
        return (
            existing
            if existing not in {"", "无法判断", "暂无法判断"}
            else extracted
        )

    proficiency_level = existing_label_or(
        extraction.proficiency_level, call.proficiency_level
    )
    emotion_state = existing_label_or(extraction.emotion_state, call.emotion_state)
    resolved_status = (
        call.resolved_status
        if call.resolved_status is not None
        else extraction.resolved_status
    )
    return replace(
        call,
        core_question=call.core_question or extraction.core_question,
        father_question=call.father_question or extraction.father_question,
        father_question_2=call.father_question_2 or extraction.father_question_2,
        agent_answer_summary=extraction.agent_answer_summary,
        demand_category=(
            call.demand_category or ", ".join(extraction.demand_categories)
        ),
        caller_type=caller_type,
        enterprise_identity=identity.identity,
        resolved_status=resolved_status,
        unresolved_reason=(
            (call.unresolved_reason or extraction.unresolved_reason)
            if resolved_status is False
            else None
        ),
        proficiency_score=extraction.proficiency_score,
        proficiency_summary=extraction.proficiency_summary,
        proficiency_level=proficiency_level,
        proficiency_basis=(
            call.proficiency_basis
            if call.proficiency_level not in {"", "无法判断", "暂无法判断"}
            else extraction.proficiency_basis
        ),
        emotion_state=emotion_state,
        emotion_basis=(
            call.emotion_basis
            if call.emotion_state not in {"", "无法判断", "暂无法判断"}
            else extraction.emotion_basis
        ),
        service_rating=extraction.service_rating,
        service_summary=extraction.service_summary,
        enterprise_identity_source=identity.source,
        enterprise_identity_conflict=identity.conflict,
    )


def enrich_call(
    call: NormalizedCallInput,
    mode: InputMode,
    client: AnalysisClient | None,
    extraction_override: ModelExtraction | None = None,
) -> tuple[NormalizedCallInput, EnrichmentMetadata]:
    """Produce the complete per-call analytical state for one ingestion mode."""

    has_text = bool(
        call.transcript
        or call.business_content
        or call.answer_content
        or call.core_question
        or call.topic_category
    )
    if not has_text:
        if mode == InputMode.TRUSTED_IMPORT:
            enriched = _local_trusted_enrichment(call)
        else:
            enriched = replace(
                call,
                demand_category="其他类",
                proficiency_level="暂无法判断",
                proficiency_basis="可用文本不足，暂不预设业务熟悉程度。",
                emotion_state="暂无法判断",
                emotion_basis="可用文本不足，暂不预设情绪状态。",
                service_rating="无法判断",
                service_summary="无法判断：可用文本不足。",
            )
        return enriched, EnrichmentMetadata(
            input_mode=mode,
            analysis_source=(
                "trusted_fields+rules"
                if mode == InputMode.TRUSTED_IMPORT
                else "insufficient_text"
            ),
            analysis_status=(
                "completed"
                if mode == InputMode.TRUSTED_IMPORT
                else "insufficient_text"
            ),
            model_name=None,
        )

    if client is None:
        if mode != InputMode.TRUSTED_IMPORT:
            raise RuntimeError("增量分析模式需要可用的大模型客户端")
        return _local_trusted_enrichment(call), EnrichmentMetadata(
            input_mode=mode,
            analysis_source="trusted_fields+rules",
            analysis_status="completed_rules_only",
            model_name=None,
        )

    payload = build_call_payload(
        transcript=call.transcript,
        business_content=call.business_content,
        answer_content=call.answer_content,
        core_question=call.core_question,
        topic_category=call.topic_category,
    )
    extraction = extraction_override
    if extraction is None:
        extraction = (
            client.analyze_history(payload)
            if mode == InputMode.TRUSTED_IMPORT
            else client.analyze_call(payload)
        )
    if mode == InputMode.TRUSTED_IMPORT:
        if not isinstance(extraction, HistoryEnrichmentResult):
            raise TypeError("历史基底模型结果类型不正确")
        enriched = _merge_history_enrichment(call, extraction)
    else:
        if not isinstance(extraction, CallExtractionResult):
            raise TypeError("增量来电模型结果类型不正确")
        enriched = _merge_incremental_extraction(call, extraction)
    return enriched, EnrichmentMetadata(
        input_mode=mode,
        analysis_source=(
            "trusted_fields+model+rules"
            if mode == InputMode.TRUSTED_IMPORT
            else "model+rules"
        ),
        analysis_status="completed",
        model_name=client.model,
    )

"""Enrich one normalized call and merge model-derived fields."""

from __future__ import annotations

from dataclasses import replace

from taxpayer_profile.analysis.contracts import (
    AnalysisClient,
    EnrichmentMetadata,
    ModelExtraction,
)
from taxpayer_profile.identity import resolve_enterprise_identity
from taxpayer_profile.ingestion.modes import InputMode
from taxpayer_profile.llm_client import CallExtractionResult, build_call_payload
from taxpayer_profile.normalization import NormalizedCallInput


def _merge_incremental_extraction(
    call: NormalizedCallInput,
    extraction: CallExtractionResult,
) -> NormalizedCallInput:
    """Merge a fresh extraction while preserving only confirmed reusable fields."""

    # New incremental inputs do not depend on a precomputed caller-type
    # column, so every regular ingestion uses the binary model result.
    caller_type = extraction.caller_type
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


def enrich_call(
    call: NormalizedCallInput,
    mode: InputMode,
    client: AnalysisClient | None,
    extraction_override: ModelExtraction | None = None,
) -> tuple[NormalizedCallInput, EnrichmentMetadata]:
    """Produce the complete analytical state for one incremental call."""

    has_text = bool(
        call.transcript
        or call.business_content
        or call.answer_content
        or call.core_question
        or call.topic_category
    )
    if not has_text:
        # Caller type is a binary required result. Persisting a text-free row
        # would reintroduce the legacy "unknown" state, so reject it instead
        # of making an arbitrary enterprise/personal assignment.
        raise ValueError(
            f"业务编号 {call.business_id} 缺少用于判定咨询主体的文本信息"
        )

    if client is None:
        raise RuntimeError("增量分析模式需要可用的大模型客户端")

    payload = build_call_payload(
        transcript=call.transcript,
        business_content=call.business_content,
        answer_content=call.answer_content,
        core_question=call.core_question,
        topic_category=call.topic_category,
    )
    extraction = extraction_override
    if extraction is None:
        extraction = client.analyze_call(payload)
    if not isinstance(extraction, CallExtractionResult):
        raise TypeError("增量来电模型结果类型不正确")
    enriched = _merge_incremental_extraction(call, extraction)
    return enriched, EnrichmentMetadata(
        input_mode=mode,
        analysis_source="model+rules",
        analysis_status="completed",
        model_name=client.model,
    )

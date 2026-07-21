"""General, versioned ingestion for trusted history and new raw workbooks."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from sqlalchemy import select

from taxpayer_profile.database import (
    create_schema,
    make_engine,
    make_session_factory,
    transactional_session,
)
from taxpayer_profile.excel_reader import (
    InputMode,
    discover_workbooks,
    read_excel_workbook,
    workbook_fingerprint,
    workbook_registration_bounds,
)
from taxpayer_profile.identity import resolve_enterprise_identity
from taxpayer_profile.llm_client import (
    PROMPT_VERSION,
    REPEAT_PROMPT_VERSION,
    CallExtractionResult,
    RepeatIssueModelResult,
    build_call_payload,
    build_repeat_payload,
)
from taxpayer_profile.models import CallerProfile, CallTrajectory, UpdateLog
from taxpayer_profile.normalization import NormalizedCallInput, normalize_call_row
from taxpayer_profile.profiling import (
    analyze_proficiency,
    analyze_service,
    classify_service_profile,
    weighted_proficiency,
)
from taxpayer_profile.repeat_analysis import RepeatDecision, analyze_repeat_issue
from taxpayer_profile.security import PhoneProtector

ANALYSIS_VERSION = "profile-2026-07-21-v3"
EXTRACTION_VERSION = "extraction-2026-07-21-v3"


class AnalysisClient(Protocol):
    model: str

    def analyze_call(self, payload: dict[str, str | None]) -> CallExtractionResult: ...

    def analyze_repeat_issue(
        self, payload: dict[str, object]
    ) -> RepeatIssueModelResult: ...


@dataclass(frozen=True)
class ProcessingSummary:
    batch_id: str
    input_filename: str
    new_call_count: int
    skipped_call_count: int
    conflict_count: int
    new_phone_count: int
    updated_profile_count: int
    repeated_call_count: int
    repeated_issue_count: int
    unresolved_count: int
    failed_count: int
    already_processed: bool = False


@dataclass(frozen=True)
class EnrichmentMetadata:
    input_mode: InputMode
    analysis_source: str
    analysis_status: str
    model_name: str | None
    analysis_error: str | None = None


def _trajectory_key(trajectory: CallTrajectory) -> tuple[datetime, str]:
    return trajectory.call_time, trajectory.business_id


def _repeat_label_is_active(trajectory: CallTrajectory) -> bool:
    if trajectory.is_repeated_issue is not True:
        return False
    expires_at = trajectory.repeat_label_expires_at
    if expires_at is None:
        return True
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
        if repeated_only and not _repeat_label_is_active(item):
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
    return "；".join(f"{index}. {question}" for index, question in enumerate(questions, 1))


def _unresolved_summary(trajectories: list[CallTrajectory], limit: int = 3) -> str | None:
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
    sentences = [
        f"该号码当前呈现“{profile.service_profile_type or '常规咨询型'}”服务画像。"
    ]
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
    if profile.proficiency_score is not None:
        sentences.append(
            f"历史沟通显示其业务熟练度约为{profile.proficiency_score:.1f}/10，"
            f"{profile.proficiency_summary or '建议在本次接待中继续观察理解程度'}"
        )
    if profile.unresolved_count:
        reason = profile.latest_unresolved_reason or "仍需核对具体处理节点"
        sentences.append(
            f"历史有{profile.unresolved_count}次未直接解决，最近原因是“{reason}”。"
        )
    elif profile.latest_service_rating:
        sentences.append(f"最近一次服务效果评估为“{profile.latest_service_rating}”。")
    return "".join(sentences)


def _update_profile(
    *, profile: CallerProfile, trajectories: list[CallTrajectory]
) -> None:
    ordered = sorted(trajectories, key=_trajectory_key)
    latest = ordered[-1]
    profile.first_call_time = ordered[0].call_time
    profile.latest_call_time = latest.call_time
    profile.total_call_count = len(ordered)
    profile.repeated_call_count = sum(item.is_repeated_call for item in ordered)
    profile.repeated_issue_count = sum(_repeat_label_is_active(item) for item in ordered)
    profile.unresolved_count = sum(item.resolved_status is False for item in ordered)
    profile.work_order_count = sum(item.work_order is True for item in ordered)
    profile.abnormal_end_count = sum(
        item.rule_abnormal_end is True or item.model_abnormal_end is True
        for item in ordered
    )
    profile.dissatisfaction_count = sum(
        item.taxpayer_dissatisfied is True for item in ordered
    )
    profile.caller_type = latest.caller_type or "无法判断"
    if profile.caller_type == "个人":
        profile.enterprise_identity = "不适用"
    elif profile.caller_type == "企业":
        profile.enterprise_identity = latest.enterprise_identity or "无法判断"
    else:
        profile.enterprise_identity = "无法判断"
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
    classification = classify_service_profile(
        total_calls=profile.total_call_count,
        repeated_issues=profile.repeated_issue_count,
        unresolved=profile.unresolved_count,
        work_orders=profile.work_order_count,
        dissatisfaction=profile.dissatisfaction_count,
        proficiency_score=profile.proficiency_score,
    )
    profile.service_profile_type = classification.profile_type
    profile.service_profile_basis = classification.basis
    profile.profile_summary = _personalized_profile_summary(profile)
    profile.updated_at = datetime.now(timezone.utc)


def _mode_for_call(
    requested_mode: InputMode,
    call: NormalizedCallInput,
    trusted_through: date | None,
) -> InputMode:
    if requested_mode != InputMode.BOOTSTRAP_MIXED:
        return requested_mode
    if trusted_through is None:
        raise ValueError("bootstrap_mixed 模式必须提供 trusted_through")
    if call.registration_time is None:
        return InputMode.RAW_ANALYSIS
    return (
        InputMode.TRUSTED_IMPORT
        if call.registration_time.date() <= trusted_through
        else InputMode.RAW_ANALYSIS
    )


def _normalize_rows(
    rows: list[dict[str, object]],
    requested_mode: InputMode,
    trusted_through: date | None,
) -> list[tuple[NormalizedCallInput, InputMode]]:
    normalized: list[tuple[NormalizedCallInput, InputMode]] = []
    for row in rows:
        registration = normalize_call_row(row, trust_analyzed_fields=False)
        row_mode = _mode_for_call(requested_mode, registration, trusted_through)
        call = normalize_call_row(
            row, trust_analyzed_fields=row_mode == InputMode.TRUSTED_IMPORT
        )
        normalized.append((call, row_mode))
    normalized.sort(
        key=lambda item: (
            item[0].call_time or datetime.max,
            item[0].business_id or "",
        )
    )
    return normalized


def _local_trusted_enrichment(call: NormalizedCallInput) -> NormalizedCallInput:
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
        demand_category=demand_category,
        unresolved_reason=unresolved_reason,
        proficiency_score=proficiency.score,
        proficiency_summary=proficiency.summary,
        service_rating=service.rating,
        service_summary=service.summary,
    )


def _merge_extraction(
    call: NormalizedCallInput,
    extraction: CallExtractionResult,
    mode: InputMode,
) -> NormalizedCallInput:
    trusted = mode == InputMode.TRUSTED_IMPORT
    caller_type = (
        call.caller_type
        if trusted and call.caller_type is not None
        else (None if extraction.caller_type == "无法判断" else extraction.caller_type)
    )
    identity = resolve_enterprise_identity(
        caller_type=caller_type,
        raw_identity=call.raw_identity_label,
        explicit_identity=extraction.explicit_enterprise_identity,
    )

    def trusted_or_extracted(existing: object, extracted: object) -> object:
        return existing if trusted and existing is not None else extracted

    resolved_status = trusted_or_extracted(
        call.resolved_status, extraction.resolved_status
    )
    unresolved_reason = None
    if resolved_status is False:
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
        core_question=call.core_question or extraction.core_question,
        demand_category=", ".join(extraction.demand_categories),
        father_question=trusted_or_extracted(  # type: ignore[arg-type]
            call.father_question, extraction.father_question
        ),
        father_question_2=trusted_or_extracted(  # type: ignore[arg-type]
            call.father_question_2, extraction.father_question_2
        ),
        caller_type=caller_type,
        enterprise_identity=identity.identity,
        resolved_status=resolved_status,  # type: ignore[arg-type]
        unresolved_reason=unresolved_reason,
        model_abnormal_end=trusted_or_extracted(  # type: ignore[arg-type]
            call.model_abnormal_end, extraction.model_abnormal_end
        ),
        waiting_expression=trusted_or_extracted(  # type: ignore[arg-type]
            call.waiting_expression, extraction.waiting_expression
        ),
        potential_pushback=trusted_or_extracted(  # type: ignore[arg-type]
            call.potential_pushback, extraction.potential_pushback
        ),
        taxpayer_dissatisfied=trusted_or_extracted(  # type: ignore[arg-type]
            call.taxpayer_dissatisfied, extraction.taxpayer_dissatisfied
        ),
        contacted_other_department=trusted_or_extracted(  # type: ignore[arg-type]
            call.contacted_other_department,
            extraction.contacted_other_department,
        ),
        active_contacted_other_department=trusted_or_extracted(  # type: ignore[arg-type]
            call.active_contacted_other_department,
            extraction.active_contacted_other_department,
        ),
        contact_target=trusted_or_extracted(  # type: ignore[arg-type]
            call.contact_target, extraction.contact_target
        ),
        natural_qa_turns=trusted_or_extracted(  # type: ignore[arg-type]
            call.natural_qa_turns, extraction.natural_qa_turns
        ),
        core_question_turns=trusted_or_extracted(  # type: ignore[arg-type]
            call.core_question_turns, extraction.core_question_turns
        ),
        effective_qa_turns=trusted_or_extracted(  # type: ignore[arg-type]
            call.effective_qa_turns, extraction.effective_qa_turns
        ),
        effective_qa_content=trusted_or_extracted(  # type: ignore[arg-type]
            call.effective_qa_content, extraction.effective_qa_content
        ),
        proficiency_score=extraction.proficiency_score,
        proficiency_summary=extraction.proficiency_summary,
        service_rating=extraction.service_rating,
        service_summary=extraction.service_summary,
        enterprise_identity_source=identity.source,
        enterprise_identity_conflict=identity.conflict,
    )


def _enrich_call(
    call: NormalizedCallInput,
    mode: InputMode,
    client: AnalysisClient | None,
) -> tuple[NormalizedCallInput, EnrichmentMetadata]:
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
                service_rating="无法判断",
                service_summary="无法判断：可用文本不足。",
            )
        return enriched, EnrichmentMetadata(
            input_mode=mode,
            analysis_source="trusted_fields+rules" if mode == InputMode.TRUSTED_IMPORT else "insufficient_text",
            analysis_status="completed" if mode == InputMode.TRUSTED_IMPORT else "insufficient_text",
            model_name=None,
        )

    if client is None:
        if mode == InputMode.RAW_ANALYSIS:
            raise RuntimeError("原始分析模式需要可用的大模型客户端")
        return _local_trusted_enrichment(call), EnrichmentMetadata(
            input_mode=mode,
            analysis_source="trusted_fields+rules",
            analysis_status="completed_rules_only",
            model_name=None,
        )

    extraction = client.analyze_call(
        build_call_payload(
            transcript=call.transcript,
            business_content=call.business_content,
            answer_content=call.answer_content,
            core_question=call.core_question,
            topic_category=call.topic_category,
        )
    )
    enriched = _merge_extraction(call, extraction, mode)
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


def _analyze_repeat_values(
    *,
    core_question: str | None,
    father_question: str | None,
    father_question_2: str | None,
    business_content: str | None,
    histories: list[CallTrajectory],
    client: AnalysisClient | None,
) -> RepeatDecision:
    repeat = analyze_repeat_issue(
        core_question=core_question,
        father_question=father_question,
        father_question_2=father_question_2,
        business_content=business_content,
        histories=histories,
    )
    if not repeat.needs_model_review:
        return repeat
    if client is None:
        return repeat
    result = client.analyze_repeat_issue(
        build_repeat_payload(
            core_question=core_question,
            father_question=father_question,
            father_question_2=father_question_2,
            business_content=business_content,
            histories=histories,
        )
    )
    if not result.is_repeated_issue:
        return RepeatDecision(
            False,
            repeat_reason=result.repeat_reason,
            summary=result.explanation,
            confidence=result.confidence,
            candidate_score=repeat.candidate_score,
            review_status="model_reviewed",
        )
    try:
        matched = histories[result.matched_history_index]  # type: ignore[index]
    except (IndexError, TypeError) as exc:
        raise RuntimeError("模型返回了无效的历史匹配序号") from exc
    return RepeatDecision(
        True,
        matched_business_id=matched.business_id,
        matched_question=(
            matched.core_question or matched.father_question or matched.father_question_2
        ),
        previous_call_time=matched.call_time,
        previous_resolved=matched.resolved_status,
        repeat_reason=result.repeat_reason,
        summary=result.explanation,
        confidence=result.confidence,
        candidate_score=repeat.candidate_score,
        review_status="model_reviewed",
    )


def _analyze_repeat(
    call: NormalizedCallInput,
    histories: list[CallTrajectory],
    client: AnalysisClient | None,
) -> RepeatDecision:
    return _analyze_repeat_values(
        core_question=call.core_question,
        father_question=call.father_question,
        father_question_2=call.father_question_2,
        business_content=call.business_content,
        histories=histories,
        client=client,
    )


def _apply_repeat_decision(
    trajectory: CallTrajectory, decision: RepeatDecision
) -> None:
    trajectory.is_repeated_issue = decision.is_repeated_issue
    trajectory.matched_previous_business_id = decision.matched_business_id
    trajectory.matched_previous_question = decision.matched_question
    trajectory.matched_previous_call_time = decision.previous_call_time
    trajectory.previous_issue_resolved = decision.previous_resolved
    trajectory.repeat_reason = decision.repeat_reason
    trajectory.repeat_summary = decision.summary
    trajectory.repeat_candidate_score = decision.candidate_score
    trajectory.repeat_confidence = decision.confidence
    trajectory.repeat_review_status = decision.review_status
    if decision.needs_model_review:
        trajectory.analysis_status = "pending_review"


def _resequence_history(trajectories: list[CallTrajectory]) -> list[CallTrajectory]:
    ordered = sorted(trajectories, key=_trajectory_key)
    for index, trajectory in enumerate(ordered):
        previous = ordered[index - 1] if index else None
        trajectory.is_repeated_call = previous is not None
        trajectory.previous_call_time = previous.call_time if previous else None
        trajectory.call_interval = (
            int((trajectory.call_time - previous.call_time).total_seconds())
            if previous
            else None
        )
        trajectory.historical_call_count = index + 1
    return ordered


def _reassess_after_backfill(
    trajectories: list[CallTrajectory],
    *,
    new_business_ids: set[str],
    client: AnalysisClient | None,
) -> list[CallTrajectory]:
    """Rebuild chronology and reconsider existing calls affected by older inserts."""

    ordered = _resequence_history(trajectories)
    new_items = [item for item in ordered if item.business_id in new_business_ids]
    if not new_items:
        return ordered
    earliest_new_key = min(_trajectory_key(item) for item in new_items)
    for index, trajectory in enumerate(ordered):
        if trajectory.business_id in new_business_ids:
            continue
        if trajectory.repeat_review_status == "manually_reviewed":
            continue
        if _trajectory_key(trajectory) <= earliest_new_key:
            continue
        decision = _analyze_repeat_values(
            core_question=trajectory.core_question,
            father_question=trajectory.father_question,
            father_question_2=trajectory.father_question_2,
            business_content=None,
            histories=ordered[:index],
            client=client,
        )
        _apply_repeat_decision(trajectory, decision)
    return ordered


def _source_record_fingerprint(call: NormalizedCallInput) -> str:
    canonical = json.dumps(
        {
            "business_id": call.business_id,
            "phone": call.phone,
            "registration_time": str(call.registration_time),
            "call_time": str(call.call_time),
            "raw_call_start_time": str(call.raw_call_start_time),
            "call_end_time": str(call.call_end_time),
            "transcript": call.transcript,
            "agent_id": call.agent_id,
            "agent_name": call.agent_name,
            "business_content": call.business_content,
            "answer_content": call.answer_content,
            "recording_path": call.recording_path,
            "registration_unit": call.registration_unit,
            "handling_method": call.handling_method,
            "business_category": call.business_category,
            "satisfaction": call.satisfaction,
            "call_serial_number": call.call_serial_number,
            "core_question": call.core_question,
            "topic_category": call.topic_category,
            "raw_identity_label": call.raw_identity_label,
            "work_order": call.work_order,
            "rule_abnormal_end": call.rule_abnormal_end,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _trajectory_from_call(
    *,
    call: NormalizedCallInput,
    phone_hash: str,
    raw_phone_encrypted: str,
    histories: list[CallTrajectory],
    repeat: RepeatDecision,
    metadata: EnrichmentMetadata,
    source_filename: str,
    source_file_fingerprint: str,
) -> CallTrajectory:
    previous = max(histories, key=_trajectory_key) if histories else None
    assert call.business_id is not None
    assert call.call_time is not None
    interval = (
        int((call.call_time - previous.call_time).total_seconds())
        if previous is not None
        else None
    )
    analysis_status = metadata.analysis_status
    if repeat.needs_model_review and analysis_status.startswith("completed"):
        analysis_status = "pending_review"
    return CallTrajectory(
        business_id=call.business_id,
        phone_hash=phone_hash,
        call_time=call.call_time,
        registration_time=call.registration_time,
        raw_call_start_time=call.raw_call_start_time,
        call_end_time=call.call_end_time,
        call_time_source=call.call_time_source,
        raw_phone_encrypted=raw_phone_encrypted,
        raw_transcript=call.transcript,
        agent_id=call.agent_id,
        agent_name=call.agent_name,
        business_content=call.business_content,
        answer_content=call.answer_content,
        recording_path=call.recording_path,
        registration_unit=call.registration_unit,
        handling_method=call.handling_method,
        business_category=call.business_category,
        satisfaction=call.satisfaction,
        call_serial_number=call.call_serial_number,
        caller_type=call.caller_type,
        enterprise_identity=call.enterprise_identity,
        core_question=call.core_question,
        topic_category=call.topic_category,
        demand_category=call.demand_category,
        father_question=call.father_question,
        father_question_2=call.father_question_2,
        resolved_status=call.resolved_status,
        unresolved_reason=call.unresolved_reason,
        work_order=call.work_order,
        rule_abnormal_end=call.rule_abnormal_end,
        model_abnormal_end=call.model_abnormal_end,
        waiting_expression=call.waiting_expression,
        potential_pushback=call.potential_pushback,
        taxpayer_dissatisfied=call.taxpayer_dissatisfied,
        contacted_other_department=call.contacted_other_department,
        active_contacted_other_department=call.active_contacted_other_department,
        contact_target=call.contact_target,
        natural_qa_turns=call.natural_qa_turns,
        core_question_turns=call.core_question_turns,
        effective_qa_turns=call.effective_qa_turns,
        effective_qa_content=call.effective_qa_content,
        proficiency_score=call.proficiency_score,
        proficiency_summary=call.proficiency_summary,
        service_rating=call.service_rating,
        service_summary=call.service_summary,
        is_repeated_call=previous is not None,
        previous_call_time=previous.call_time if previous is not None else None,
        call_interval=interval,
        historical_call_count=len(histories) + 1,
        is_repeated_issue=repeat.is_repeated_issue,
        matched_previous_business_id=repeat.matched_business_id,
        matched_previous_question=repeat.matched_question,
        matched_previous_call_time=repeat.previous_call_time,
        previous_issue_resolved=repeat.previous_resolved,
        repeat_reason=repeat.repeat_reason,
        repeat_summary=repeat.summary,
        repeat_candidate_score=repeat.candidate_score,
        repeat_confidence=repeat.confidence,
        repeat_review_status=repeat.review_status,
        input_mode=metadata.input_mode.value,
        analysis_source=metadata.analysis_source,
        model_name=metadata.model_name,
        prompt_version=(
            f"{PROMPT_VERSION};{REPEAT_PROMPT_VERSION}"
            if metadata.model_name
            else None
        ),
        extraction_version=EXTRACTION_VERSION,
        enterprise_identity_source=call.enterprise_identity_source,
        enterprise_identity_conflict=call.enterprise_identity_conflict,
        source_filename=source_filename,
        source_file_fingerprint=source_file_fingerprint,
        source_record_fingerprint=_source_record_fingerprint(call),
        analysis_error=metadata.analysis_error,
        analysis_status=analysis_status,
        analysis_version=ANALYSIS_VERSION,
    )


def process_workbook(
    *,
    input_path: Path | str,
    database_path: Path | str,
    protector: PhoneProtector,
    llm_client: AnalysisClient | None = None,
    input_mode: InputMode = InputMode.TRUSTED_IMPORT,
    trusted_through: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> ProcessingSummary:
    """Analyze and atomically ingest one independent workbook."""

    source = Path(input_path).expanduser().resolve()
    file_fingerprint = workbook_fingerprint(source)
    engine = make_engine(database_path)
    create_schema(engine)
    sessions = make_session_factory(engine)
    with sessions() as read_session:
        completed_log = read_session.scalar(
            select(UpdateLog).where(
                UpdateLog.input_fingerprint == file_fingerprint,
                UpdateLog.status == "completed",
            )
        )
    if completed_log is not None:
        return ProcessingSummary(
            batch_id=completed_log.batch_id,
            input_filename=source.name,
            new_call_count=0,
            skipped_call_count=completed_log.source_row_count or 0,
            conflict_count=0,
            new_phone_count=0,
            updated_profile_count=0,
            repeated_call_count=0,
            repeated_issue_count=0,
            unresolved_count=0,
            failed_count=0,
            already_processed=True,
        )

    frame = read_excel_workbook(source, start_date=start_date, end_date=end_date)
    rows = frame.to_dict(orient="records")
    with sessions() as read_session:
        existing_trajectories = list(
            read_session.scalars(
                select(CallTrajectory).order_by(CallTrajectory.call_time)
            )
        )
        initial_profile_hashes = set(
            read_session.scalars(select(CallerProfile.phone_hash))
        )

    calls = _normalize_rows(rows, input_mode, trusted_through)
    histories_by_phone: dict[str, list[CallTrajectory]] = defaultdict(list)
    for trajectory in existing_trajectories:
        histories_by_phone[trajectory.phone_hash].append(trajectory)
    existing_by_business = {item.business_id: item for item in existing_trajectories}

    new_trajectories: list[CallTrajectory] = []
    encrypted_phones: dict[str, str] = {}
    affected_hashes: set[str] = set()
    skipped = conflicts = failed = repeated_calls = repeated_issues = unresolved = 0
    failure_reasons: Counter[str] = Counter()
    consecutive_model_failures = 0

    total_calls = len(calls)
    for position, (call, row_mode) in enumerate(calls, start=1):
        if progress_callback is not None:
            progress_callback(position, total_calls, "开始处理")
        if call.business_id is None or call.phone is None or call.call_time is None:
            failed += 1
            if progress_callback is not None:
                progress_callback(position, total_calls, "输入字段无效")
            continue
        record_fingerprint = _source_record_fingerprint(call)
        existing = existing_by_business.get(call.business_id)
        if existing is not None:
            if existing.source_record_fingerprint not in {None, record_fingerprint}:
                conflicts += 1
            else:
                skipped += 1
            if progress_callback is not None:
                progress_callback(
                    position,
                    total_calls,
                    "业务编号冲突" if existing.source_record_fingerprint not in {None, record_fingerprint} else "已跳过",
                )
            continue
        phone_hash = protector.hash_phone(call.phone)
        all_phone_histories = histories_by_phone[phone_hash]
        current_key = (call.call_time, call.business_id)
        histories = [
            item for item in all_phone_histories if _trajectory_key(item) < current_key
        ]
        try:
            enriched, metadata = _enrich_call(call, row_mode, llm_client)
            repeat = _analyze_repeat(enriched, histories, llm_client)
            consecutive_model_failures = 0
        except RuntimeError as exc:
            failed += 1
            cause = exc.__cause__
            failure_reasons[type(cause).__name__ if cause else type(exc).__name__] += 1
            consecutive_model_failures += 1
            if progress_callback is not None:
                progress_callback(position, total_calls, "模型分析失败")
            if consecutive_model_failures >= 3:
                raise RuntimeError(
                    "连续 3 条模型分析失败，已熔断；本文件没有写入数据库"
                ) from exc
            continue
        except Exception as exc:
            failed += 1
            failure_reasons[type(exc).__name__] += 1
            if progress_callback is not None:
                progress_callback(position, total_calls, "处理失败")
            continue
        encrypted_phone = protector.encrypt_phone(call.phone)
        trajectory = _trajectory_from_call(
            call=enriched,
            phone_hash=phone_hash,
            raw_phone_encrypted=encrypted_phone,
            histories=histories,
            repeat=repeat,
            metadata=metadata,
            source_filename=source.name,
            source_file_fingerprint=file_fingerprint,
        )
        new_trajectories.append(trajectory)
        all_phone_histories.append(trajectory)
        all_phone_histories.sort(key=_trajectory_key)
        existing_by_business[call.business_id] = trajectory
        affected_hashes.add(phone_hash)
        encrypted_phones.setdefault(phone_hash, encrypted_phone)
        repeated_calls += int(trajectory.is_repeated_call)
        repeated_issues += int(trajectory.is_repeated_issue is True)
        unresolved += int(trajectory.resolved_status is False)
        if progress_callback is not None:
            progress_callback(position, total_calls, "处理完成")

    new_business_ids = {item.business_id for item in new_trajectories}
    for phone_hash in affected_hashes:
        histories_by_phone[phone_hash] = _reassess_after_backfill(
            histories_by_phone[phone_hash],
            new_business_ids=new_business_ids,
            client=llm_client,
        )
    repeated_calls = sum(item.is_repeated_call for item in new_trajectories)
    repeated_issues = sum(_repeat_label_is_active(item) for item in new_trajectories)

    batch_id = uuid4().hex
    started_at = datetime.now(timezone.utc)
    valid_dates = [
        call.registration_time.date()
        for call, _ in calls
        if call.registration_time is not None
    ]
    date_range = (
        f"{min(valid_dates).isoformat()}..{max(valid_dates).isoformat()}"
        if valid_dates
        else "unknown"
    )
    with transactional_session(sessions) as session:
        for phone_hash in affected_hashes:
            for trajectory in histories_by_phone[phone_hash]:
                if trajectory.business_id in new_business_ids:
                    session.add(trajectory)
                else:
                    session.merge(trajectory)
        profiles = {
            item.phone_hash: item for item in session.scalars(select(CallerProfile))
        }
        for phone_hash in affected_hashes:
            history = histories_by_phone[phone_hash]
            profile = profiles.get(phone_hash)
            if profile is None:
                first = min(history, key=lambda item: item.call_time)
                profile = CallerProfile(
                    phone_hash=phone_hash,
                    phone_encrypted=encrypted_phones[phone_hash],
                    first_call_time=first.call_time,
                    latest_call_time=first.call_time,
                )
                profiles[phone_hash] = profile
                session.add(profile)
            _update_profile(profile=profile, trajectories=history)
        session.add(
            UpdateLog(
                batch_id=batch_id,
                data_date=date_range,
                input_filename=source.name,
                input_fingerprint=file_fingerprint,
                input_mode=input_mode.value,
                analysis_version=ANALYSIS_VERSION,
                source_row_count=len(rows),
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                new_call_count=len(new_trajectories),
                new_phone_count=len(affected_hashes.difference(initial_profile_hashes)),
                updated_profile_count=len(affected_hashes),
                repeated_call_count=repeated_calls,
                repeated_issue_count=repeated_issues,
                unresolved_count=unresolved,
                failed_count=failed,
                skipped_count=skipped,
                conflict_count=conflicts,
                status="completed" if failed == 0 else "completed_with_errors",
                summary=(
                    f"新增 {len(new_trajectories)}，跳过 {skipped}，"
                    f"冲突 {conflicts}，失败 {failed}。"
                    + (
                        "失败类型："
                        + "、".join(
                            f"{name}={count}"
                            for name, count in sorted(failure_reasons.items())
                        )
                        if failure_reasons
                        else ""
                    )
                ),
            )
        )

    return ProcessingSummary(
        batch_id=batch_id,
        input_filename=source.name,
        new_call_count=len(new_trajectories),
        skipped_call_count=skipped,
        conflict_count=conflicts,
        new_phone_count=len(affected_hashes.difference(initial_profile_hashes)),
        updated_profile_count=len(affected_hashes),
        repeated_call_count=repeated_calls,
        repeated_issue_count=repeated_issues,
        unresolved_count=unresolved,
        failed_count=failed,
    )


def process_raw_directory(
    *,
    raw_directory: Path | str,
    database_path: Path | str,
    protector: PhoneProtector,
    llm_client: AnalysisClient | None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> list[ProcessingSummary]:
    """Process every independent workbook in a raw directory in name order."""

    workbooks = discover_workbooks(raw_directory)
    ordered = sorted(
        (
            (*workbook_registration_bounds(workbook), workbook)
            for workbook in workbooks
        ),
        key=lambda item: (item[0], item[1], item[2].name.lower()),
    )
    return [
        process_workbook(
            input_path=workbook,
            database_path=database_path,
            protector=protector,
            llm_client=llm_client,
            input_mode=InputMode.RAW_ANALYSIS,
            progress_callback=progress_callback,
        )
        for _, _, workbook in ordered
    ]


def rebuild_all_profiles(database_path: Path | str) -> int:
    engine = make_engine(database_path)
    create_schema(engine)
    sessions = make_session_factory(engine)
    with transactional_session(sessions) as session:
        histories: dict[str, list[CallTrajectory]] = defaultdict(list)
        for trajectory in session.scalars(
            select(CallTrajectory).order_by(CallTrajectory.call_time)
        ):
            histories[trajectory.phone_hash].append(trajectory)
        profiles = {
            profile.phone_hash: profile
            for profile in session.scalars(select(CallerProfile))
        }
        for phone_hash, trajectories in histories.items():
            profile = profiles.get(phone_hash)
            if profile is not None:
                _update_profile(profile=profile, trajectories=trajectories)
        return len(histories)

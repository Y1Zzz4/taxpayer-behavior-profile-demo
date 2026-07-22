"""General, versioned ingestion for trusted history and new raw workbooks."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

import httpx
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
    HISTORY_ENRICHMENT_PROMPT_VERSION,
    PROMPT_VERSION,
    REPEAT_PROMPT_VERSION,
    CallExtractionResult,
    HistoryEnrichmentResult,
    RepeatIssueModelResult,
    build_call_payload,
    build_repeat_payload,
)
from taxpayer_profile.models import CallerProfile, CallTrajectory, UpdateLog
from taxpayer_profile.normalization import NormalizedCallInput, normalize_call_row
from taxpayer_profile.profiling import (
    analyze_proficiency,
    analyze_service,
    infer_emotion_state,
    normalize_proficiency_level,
    weighted_proficiency,
)
from taxpayer_profile.repeat_analysis import RepeatDecision, analyze_repeat_issue
from taxpayer_profile.security import PhoneProtector

ANALYSIS_VERSION = "profile-2026-07-22-v6"
EXTRACTION_VERSION = "extraction-2026-07-22-v6"


class AnalysisClient(Protocol):
    model: str

    def analyze_call(self, payload: dict[str, str | None]) -> CallExtractionResult: ...

    def analyze_history(
        self, payload: dict[str, str | None]
    ) -> HistoryEnrichmentResult: ...

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


CachedModelResult = (
    CallExtractionResult | HistoryEnrichmentResult | RepeatIssueModelResult
)
ModelExtraction = CallExtractionResult | HistoryEnrichmentResult


class ModelExtractionCache:
    """Persistent, privacy-minimized cache for validated per-call model outputs."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS extraction_cache (
                cache_key TEXT PRIMARY KEY,
                result_kind TEXT NOT NULL,
                result_json TEXT NOT NULL,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def get(
        self, cache_key: str, result_type: type[CachedModelResult]
    ) -> CachedModelResult | None:
        row = self._connection.execute(
            "SELECT result_kind, result_json FROM extraction_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None or row[0] != result_type.__name__:
            return None
        try:
            return result_type.model_validate_json(row[1])
        except ValueError:
            self._connection.execute(
                "DELETE FROM extraction_cache WHERE cache_key = ?", (cache_key,)
            )
            self._connection.commit()
            return None

    def put(
        self,
        cache_key: str,
        result: CachedModelResult,
        *,
        model_name: str,
        prompt_version: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO extraction_cache (
                cache_key, result_kind, result_json, model_name,
                prompt_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key,
                type(result).__name__,
                result.model_dump_json(),
                model_name,
                prompt_version,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()


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
    sentences: list[str] = []
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
    if profile.proficiency_level:
        sentences.append(
            f"近期沟通显示其业务专业度为{profile.proficiency_level}，"
            f"{profile.proficiency_basis or '建议在本次接待中继续观察理解程度'}"
        )
    if profile.emotion_state:
        sentences.append(
            f"近期情绪状态为{profile.emotion_state}，"
            f"{profile.emotion_basis or '建议根据本次表达动态调整沟通方式'}"
        )
    if profile.unresolved_count:
        reason = profile.latest_unresolved_reason or "仍需核对具体处理节点"
        sentences.append(
            f"历史有{profile.unresolved_count}次未直接解决，最近原因是“{reason}”。"
        )
    elif profile.latest_service_rating:
        sentences.append(f"最近一次服务效果评估为“{profile.latest_service_rating}”。")
    return "".join(sentences)


def _recent_five_workday_items(
    ordered: list[CallTrajectory],
) -> list[CallTrajectory]:
    anchor = ordered[-1].call_time.date()
    workdays: set[date] = set()
    current = anchor
    while len(workdays) < 5:
        if current.weekday() < 5:
            workdays.add(current)
        current -= timedelta(days=1)
    return [item for item in ordered if item.call_time.date() in workdays]


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
    recent = _recent_five_workday_items(ordered)
    recent_level = next(
        (
            item
            for item in reversed(recent)
            if normalize_proficiency_level(
                item.proficiency_level, item.proficiency_score
            )
            != "暂无法判断"
        ),
        latest,
    )
    profile.proficiency_level = normalize_proficiency_level(
        recent_level.proficiency_level, recent_level.proficiency_score
    )
    profile.proficiency_basis = (
        recent_level.proficiency_basis
        or recent_level.proficiency_summary
        or "近期可用表达不足，暂不预设业务熟悉程度。"
    )
    recent_emotion = next(
        (
            item
            for item in reversed(recent)
            if item.emotion_state in {"平稳", "焦虑", "不满"}
        ),
        latest,
    )
    profile.emotion_state = recent_emotion.emotion_state or "暂无法判断"
    profile.emotion_basis = (
        recent_emotion.emotion_basis or "近期可用表达不足，暂不预设情绪状态。"
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
    profile.service_profile_type = None
    profile.service_profile_basis = None
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

    def trusted_new_label(existing: str, extracted: str) -> str:
        if trusted and existing not in {"", "无法判断", "暂无法判断"}:
            return existing
        return extracted

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
        agent_answer_summary=extraction.agent_answer_summary,
        demand_category=(
            call.demand_category
            if trusted and call.demand_category
            else ", ".join(extraction.demand_categories)
        ),
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
        proficiency_level=trusted_new_label(
            call.proficiency_level, extraction.proficiency_level
        ),
        proficiency_basis=(
            call.proficiency_basis
            if trusted
            and call.proficiency_level not in {"", "无法判断", "暂无法判断"}
            else extraction.proficiency_basis
        ),
        emotion_state=trusted_new_label(call.emotion_state, extraction.emotion_state),
        emotion_basis=(
            call.emotion_basis
            if trusted
            and call.emotion_state not in {"", "无法判断", "暂无法判断"}
            else extraction.emotion_basis
        ),
        service_rating=extraction.service_rating,
        service_summary=extraction.service_summary,
        enterprise_identity_source=identity.source,
        enterprise_identity_conflict=identity.conflict,
    )


def _merge_history_enrichment(
    call: NormalizedCallInput,
    extraction: HistoryEnrichmentResult,
) -> NormalizedCallInput:
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


def _enrich_call(
    call: NormalizedCallInput,
    mode: InputMode,
    client: AnalysisClient | None,
    extraction_override: ModelExtraction | None = None,
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
                proficiency_level="暂无法判断",
                proficiency_basis="可用文本不足，暂不预设业务熟悉程度。",
                emotion_state="暂无法判断",
                emotion_basis="可用文本不足，暂不预设情绪状态。",
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
            raise TypeError("原始来电模型结果类型不正确")
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
    result_override: RepeatIssueModelResult | None = None,
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
    result = result_override
    if result is None:
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
    result_override: RepeatIssueModelResult | None = None,
) -> RepeatDecision:
    return _analyze_repeat_values(
        core_question=call.core_question,
        father_question=call.father_question,
        father_question_2=call.father_question_2,
        business_content=call.business_content,
        histories=histories,
        client=client,
        result_override=result_override,
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
            "secondary_topic": call.secondary_topic,
            "raw_identity_label": call.raw_identity_label,
            "work_order": call.work_order,
            "rule_abnormal_end": call.rule_abnormal_end,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _model_cache_key(
    call: NormalizedCallInput, mode: InputMode, client: AnalysisClient
) -> tuple[str, str]:
    prompt_version = (
        HISTORY_ENRICHMENT_PROMPT_VERSION
        if mode == InputMode.TRUSTED_IMPORT
        else PROMPT_VERSION
    )
    value = "|".join(
        (
            _source_record_fingerprint(call),
            mode.value,
            client.model,
            prompt_version,
        )
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest(), prompt_version


def _extract_model_fields(
    call: NormalizedCallInput,
    mode: InputMode,
    client: AnalysisClient,
) -> ModelExtraction:
    payload = build_call_payload(
        transcript=call.transcript,
        business_content=call.business_content,
        answer_content=call.answer_content,
        core_question=call.core_question,
        topic_category=call.topic_category,
    )
    return (
        client.analyze_history(payload)
        if mode == InputMode.TRUSTED_IMPORT
        else client.analyze_call(payload)
    )


def _is_model_pressure_error(error: Exception) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, httpx.TimeoutException):
            return True
        if isinstance(current, httpx.HTTPStatusError) and (
            current.response.status_code == 429
            or current.response.status_code >= 500
        ):
            return True
        current = current.__cause__
    return False


def _prefetch_model_extractions(
    *,
    calls: list[tuple[NormalizedCallInput, InputMode]],
    existing_business_ids: set[str],
    client: AnalysisClient | None,
    workers: int,
    cache_path: Path | str | None,
    progress_callback: Callable[[int, int, str], None] | None,
) -> dict[str, ModelExtraction | Exception]:
    if client is None:
        return {}
    if not 1 <= workers <= 16:
        raise ValueError("模型并发数必须在 1—16 之间")
    pending: list[tuple[str, NormalizedCallInput, InputMode]] = []
    seen: set[str] = set()
    for call, mode in calls:
        business_id = call.business_id
        if (
            business_id is None
            or business_id in seen
            or business_id in existing_business_ids
            or call.phone is None
            or call.call_time is None
            or not (
                call.transcript
                or call.business_content
                or call.answer_content
                or call.core_question
                or call.topic_category
            )
        ):
            continue
        seen.add(business_id)
        pending.append((business_id, call, mode))
    if not pending:
        return {}

    cache = ModelExtractionCache(cache_path) if cache_path is not None else None
    results: dict[str, ModelExtraction | Exception] = {}
    futures: dict[Future[ModelExtraction], tuple[str, str, str]] = {}
    uncached: list[
        tuple[str, NormalizedCallInput, InputMode, str, str]
    ] = []
    completed = 0
    total = len(pending)
    try:
        for business_id, call, mode in pending:
            cache_key, prompt_version = _model_cache_key(call, mode, client)
            result_type: type[CachedModelResult] = (
                HistoryEnrichmentResult
                if mode == InputMode.TRUSTED_IMPORT
                else CallExtractionResult
            )
            cached = cache.get(cache_key, result_type) if cache else None
            if cached is not None:
                results[business_id] = cached
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, total, "模型缓存命中")
                continue
            uncached.append(
                (business_id, call, mode, cache_key, prompt_version)
            )

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="model") as pool:
            queued = iter(uncached)

            def submit_next() -> bool:
                try:
                    business_id, call, mode, cache_key, prompt_version = next(queued)
                except StopIteration:
                    return False
                future = pool.submit(_extract_model_fields, call, mode, client)
                futures[future] = (business_id, cache_key, prompt_version)
                return True

            for _ in range(min(workers, len(uncached))):
                submit_next()
            consecutive_failures = 0
            active_limit = workers
            success_streak = 0
            while futures:
                done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
                for future in done:
                    business_id, cache_key, prompt_version = futures.pop(future)
                    try:
                        result = future.result()
                        results[business_id] = result
                        if cache is not None:
                            cache.put(
                                cache_key,
                                result,
                                model_name=client.model,
                                prompt_version=prompt_version,
                            )
                        status = "模型提取完成"
                        consecutive_failures = 0
                        success_streak += 1
                        if success_streak >= 25 and active_limit < workers:
                            active_limit += 1
                            success_streak = 0
                    except Exception as exc:
                        results[business_id] = exc
                        status = "模型提取失败"
                        consecutive_failures += 1
                        success_streak = 0
                        if _is_model_pressure_error(exc) and active_limit > 1:
                            active_limit -= 1
                            status = f"模型压力过高，并发已降至{active_limit}"
                    completed += 1
                    if progress_callback is not None:
                        progress_callback(completed, total, status)
                    if consecutive_failures >= 3:
                        for queued_future in futures:
                            queued_future.cancel()
                        raise RuntimeError(
                            "连续 3 条模型提取失败，已熔断"
                            + ("；成功结果已写入断点缓存" if cache else "")
                        ) from results[business_id]
                while len(futures) < active_limit and submit_next():
                    pass
    finally:
        if cache is not None:
            cache.close()
    return results


PreparedEnrichment = tuple[NormalizedCallInput, EnrichmentMetadata]


def _prepare_enrichments_and_repeat_payloads(
    *,
    calls: list[tuple[NormalizedCallInput, InputMode]],
    existing_trajectories: list[CallTrajectory],
    existing_business_ids: set[str],
    prefetched_extractions: dict[str, ModelExtraction | Exception],
    client: AnalysisClient | None,
    protector: PhoneProtector,
) -> tuple[
    dict[str, PreparedEnrichment | Exception],
    dict[str, dict[str, object]],
]:
    if client is None:
        return {}, {}
    histories_by_phone: dict[str, list[CallTrajectory]] = defaultdict(list)
    for trajectory in existing_trajectories:
        histories_by_phone[trajectory.phone_hash].append(trajectory)
    prepared: dict[str, PreparedEnrichment | Exception] = {}
    repeat_payloads: dict[str, dict[str, object]] = {}
    seen = set(existing_business_ids)
    for call, mode in calls:
        business_id = call.business_id
        if (
            business_id is None
            or business_id in seen
            or call.phone is None
            or call.call_time is None
        ):
            continue
        seen.add(business_id)
        prefetched = prefetched_extractions.get(business_id)
        if isinstance(prefetched, Exception):
            prepared[business_id] = prefetched
            continue
        try:
            enriched, metadata = _enrich_call(
                call,
                mode,
                client,
                extraction_override=prefetched,
            )
        except Exception as exc:
            prepared[business_id] = exc
            continue
        prepared[business_id] = (enriched, metadata)
        phone_hash = protector.hash_phone(call.phone)
        current_key = (call.call_time, business_id)
        histories = [
            item
            for item in histories_by_phone[phone_hash]
            if _trajectory_key(item) < current_key
        ]
        local_repeat = analyze_repeat_issue(
            core_question=enriched.core_question,
            father_question=enriched.father_question,
            father_question_2=enriched.father_question_2,
            business_content=enriched.business_content,
            histories=histories,
        )
        if local_repeat.needs_model_review:
            repeat_payloads[business_id] = build_repeat_payload(
                core_question=enriched.core_question,
                father_question=enriched.father_question,
                father_question_2=enriched.father_question_2,
                business_content=enriched.business_content,
                histories=histories,
            )
        placeholder = CallTrajectory(
            business_id=business_id,
            phone_hash=phone_hash,
            call_time=call.call_time,
            core_question=enriched.core_question,
            father_question=enriched.father_question,
            father_question_2=enriched.father_question_2,
            resolved_status=enriched.resolved_status,
            analysis_status="prepared",
            analysis_version=ANALYSIS_VERSION,
        )
        histories_by_phone[phone_hash].append(placeholder)
        histories_by_phone[phone_hash].sort(key=_trajectory_key)
    return prepared, repeat_payloads


def _repeat_cache_key(
    payload: dict[str, object], client: AnalysisClient
) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    value = "|".join((canonical, client.model, REPEAT_PROMPT_VERSION))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _prefetch_repeat_reviews(
    *,
    payloads: dict[str, dict[str, object]],
    client: AnalysisClient | None,
    workers: int,
    cache_path: Path | str | None,
    progress_callback: Callable[[int, int, str], None] | None,
) -> dict[str, RepeatIssueModelResult | Exception]:
    if client is None or not payloads:
        return {}
    cache = ModelExtractionCache(cache_path) if cache_path is not None else None
    results: dict[str, RepeatIssueModelResult | Exception] = {}
    pending: list[tuple[str, dict[str, object], str]] = []
    completed = 0
    total = len(payloads)
    try:
        for business_id, payload in payloads.items():
            cache_key = _repeat_cache_key(payload, client)
            cached = cache.get(cache_key, RepeatIssueModelResult) if cache else None
            if isinstance(cached, RepeatIssueModelResult):
                results[business_id] = cached
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, total, "重复诉求缓存命中")
            else:
                pending.append((business_id, payload, cache_key))

        futures: dict[
            Future[RepeatIssueModelResult], tuple[str, str]
        ] = {}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="repeat") as pool:
            queued = iter(pending)

            def submit_next() -> bool:
                try:
                    business_id, payload, cache_key = next(queued)
                except StopIteration:
                    return False
                future = pool.submit(client.analyze_repeat_issue, payload)
                futures[future] = (business_id, cache_key)
                return True

            for _ in range(min(workers, len(pending))):
                submit_next()
            active_limit = workers
            success_streak = 0
            consecutive_failures = 0
            while futures:
                done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
                for future in done:
                    business_id, cache_key = futures.pop(future)
                    try:
                        result = future.result()
                        results[business_id] = result
                        if cache is not None:
                            cache.put(
                                cache_key,
                                result,
                                model_name=client.model,
                                prompt_version=REPEAT_PROMPT_VERSION,
                            )
                        status = "重复诉求复核完成"
                        consecutive_failures = 0
                        success_streak += 1
                        if success_streak >= 25 and active_limit < workers:
                            active_limit += 1
                            success_streak = 0
                    except Exception as exc:
                        results[business_id] = exc
                        status = "重复诉求复核失败"
                        consecutive_failures += 1
                        success_streak = 0
                        if _is_model_pressure_error(exc) and active_limit > 1:
                            active_limit -= 1
                            status = f"模型压力过高，并发已降至{active_limit}"
                    completed += 1
                    if progress_callback is not None:
                        progress_callback(completed, total, status)
                    if consecutive_failures >= 3:
                        for queued_future in futures:
                            queued_future.cancel()
                        raise RuntimeError(
                            "连续 3 条重复诉求模型复核失败，已熔断"
                            + ("；成功结果已写入断点缓存" if cache else "")
                        ) from results[business_id]
                while len(futures) < active_limit and submit_next():
                    pass
    finally:
        if cache is not None:
            cache.close()
    return results


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
        agent_answer_summary=call.agent_answer_summary,
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
        secondary_topic=call.secondary_topic,
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
        proficiency_level=call.proficiency_level,
        proficiency_basis=call.proficiency_basis,
        emotion_state=call.emotion_state,
        emotion_basis=call.emotion_basis,
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
            f"{HISTORY_ENRICHMENT_PROMPT_VERSION if metadata.input_mode == InputMode.TRUSTED_IMPORT else PROMPT_VERSION};"
            f"{REPEAT_PROMPT_VERSION}"
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
    model_workers: int = 1,
    extraction_cache_path: Path | str | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> ProcessingSummary:
    """Analyze and atomically ingest one independent workbook."""

    source = Path(input_path).expanduser().resolve()
    file_fingerprint = workbook_fingerprint(source)
    processing_fingerprint = hashlib.sha256(
        "|".join(
            (
                file_fingerprint,
                input_mode.value,
                trusted_through.isoformat() if trusted_through else "",
                start_date.isoformat() if start_date else "",
                end_date.isoformat() if end_date else "",
                ANALYSIS_VERSION,
            )
        ).encode("utf-8")
    ).hexdigest()
    engine = make_engine(database_path)
    create_schema(engine)
    sessions = make_session_factory(engine)
    with sessions() as read_session:
        completed_log = read_session.scalar(
            select(UpdateLog).where(
                UpdateLog.input_fingerprint == processing_fingerprint,
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
    prefetched_extractions = _prefetch_model_extractions(
        calls=calls,
        existing_business_ids=set(existing_by_business),
        client=llm_client,
        workers=model_workers,
        cache_path=extraction_cache_path,
        progress_callback=progress_callback,
    )
    prepared_enrichments, repeat_payloads = _prepare_enrichments_and_repeat_payloads(
        calls=calls,
        existing_trajectories=existing_trajectories,
        existing_business_ids=set(existing_by_business),
        prefetched_extractions=prefetched_extractions,
        client=llm_client,
        protector=protector,
    )
    prefetched_repeat_reviews = _prefetch_repeat_reviews(
        payloads=repeat_payloads,
        client=llm_client,
        workers=model_workers,
        cache_path=extraction_cache_path,
        progress_callback=progress_callback,
    )

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
            prepared = prepared_enrichments.get(call.business_id)
            if isinstance(prepared, Exception):
                raise prepared
            if prepared is None:
                prefetched = prefetched_extractions.get(call.business_id)
                if isinstance(prefetched, Exception):
                    raise prefetched
                enriched, metadata = _enrich_call(
                    call,
                    row_mode,
                    llm_client,
                    extraction_override=prefetched,
                )
            else:
                enriched, metadata = prepared
            repeat_review = prefetched_repeat_reviews.get(call.business_id)
            if isinstance(repeat_review, Exception):
                raise repeat_review
            repeat = _analyze_repeat(
                enriched,
                histories,
                llm_client,
                result_override=repeat_review,
            )
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
                input_fingerprint=processing_fingerprint,
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

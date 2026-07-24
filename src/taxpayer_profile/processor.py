"""Versioned orchestration for legacy history and incremental workbooks."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

import httpx
from sqlalchemy import select

from taxpayer_profile.analysis.cache import CachedModelResult, ModelExtractionCache
from taxpayer_profile.analysis.contracts import (
    AnalysisClient,
    EnrichmentMetadata,
    ModelExtraction,
)
from taxpayer_profile.analysis.enrichment import enrich_call
from taxpayer_profile.database import (
    create_schema,
    make_engine,
    make_session_factory,
    transactional_session,
)
from taxpayer_profile.ingestion.excel import (
    discover_workbooks,
    read_excel_workbook,
    workbook_fingerprint,
    workbook_registration_bounds,
)
from taxpayer_profile.ingestion.modes import InputMode
from taxpayer_profile.ingestion.policy import (
    INCREMENTAL_REUSE_POLICY,
    TRUSTED_HISTORY_REUSE_POLICY,
)
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
from taxpayer_profile.profiles.aggregation import (
    repeat_label_is_active as _repeat_label_is_active,
    trajectory_key as _trajectory_key,
    update_profile as _update_profile,
)
from taxpayer_profile.repeat_analysis import RepeatDecision, analyze_repeat_issue
from taxpayer_profile.security import PhoneProtector

ANALYSIS_VERSION = "profile-2026-07-22-v6"
EXTRACTION_VERSION = "extraction-2026-07-22-v6"


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
        # Mode selection needs only source dates. Using the incremental policy
        # here also prevents legacy analytical fields from influencing routing.
        registration = normalize_call_row(row, reuse_policy=INCREMENTAL_REUSE_POLICY)
        row_mode = _mode_for_call(requested_mode, registration, trusted_through)
        call = normalize_call_row(
            row,
            reuse_policy=(
                TRUSTED_HISTORY_REUSE_POLICY
                if row_mode == InputMode.TRUSTED_IMPORT
                else INCREMENTAL_REUSE_POLICY
            ),
        )
        normalized.append((call, row_mode))
    normalized.sort(
        key=lambda item: (
            item[0].call_time or datetime.max,
            item[0].business_id or "",
        )
    )
    return normalized


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
            enriched, metadata = enrich_call(
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
                enriched, metadata = enrich_call(
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
            input_mode=InputMode.INCREMENTAL,
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

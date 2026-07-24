"""Application use cases for legacy history and incremental ingestion."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from sqlalchemy import select

from taxpayer_profile.analysis.batch import (
    prefetch_model_extractions,
    prefetch_repeat_reviews,
)
from taxpayer_profile.analysis.contracts import (
    AnalysisClient,
    EnrichmentMetadata,
    ModelExtraction,
)
from taxpayer_profile.analysis.enrichment import enrich_call
from taxpayer_profile.analysis.repetition import (
    analyze_repeat as _analyze_repeat,
    reassess_after_backfill as _reassess_after_backfill,
)
from taxpayer_profile.database import (
    create_schema,
    make_engine,
    make_session_factory,
    transactional_session,
)
from taxpayer_profile.ingestion.contracts import TabularInputAdapter
from taxpayer_profile.ingestion.excel import (
    ExcelInputAdapter,
    discover_workbooks,
    workbook_registration_bounds,
)
from taxpayer_profile.ingestion.fingerprint import (
    batch_processing_fingerprint,
    source_record_fingerprint as _source_record_fingerprint,
)
from taxpayer_profile.ingestion.modes import InputMode
from taxpayer_profile.ingestion.policy import (
    INCREMENTAL_REUSE_POLICY,
    TRUSTED_HISTORY_REUSE_POLICY,
)
from taxpayer_profile.ingestion.schema import validate_input_rows
from taxpayer_profile.llm_client import (
    HISTORY_ENRICHMENT_PROMPT_VERSION,
    PROMPT_VERSION,
    REPEAT_PROMPT_VERSION,
    RepeatIssueModelResult,
    build_repeat_payload,
)
from taxpayer_profile.models import (
    CallerProfile,
    CallTrajectory,
    IngestionConflict,
    UpdateLog,
)
from taxpayer_profile.normalization import NormalizedCallInput, normalize_call_row
from taxpayer_profile.observability import log_event
from taxpayer_profile.profiles.aggregation import (
    repeat_label_is_active as _repeat_label_is_active,
    trajectory_key as _trajectory_key,
    update_profile as _update_profile,
)
from taxpayer_profile.repeat_analysis import RepeatDecision, analyze_repeat_issue
from taxpayer_profile.security import PhoneProtector

ANALYSIS_VERSION = "profile-2026-07-22-v6"
EXTRACTION_VERSION = "extraction-2026-07-22-v6"
LOGGER = logging.getLogger(__name__)


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
    input_mode: InputMode = InputMode.INCREMENTAL,
    trusted_through: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    model_workers: int = 1,
    extraction_cache_path: Path | str | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    input_adapter: TabularInputAdapter | None = None,
) -> ProcessingSummary:
    """Analyze and atomically ingest one independent tabular source."""

    source = Path(input_path).expanduser().resolve()
    adapter = input_adapter or ExcelInputAdapter()
    source_identity = adapter.identify(source)
    source_name = source_identity.name
    file_fingerprint = source_identity.fingerprint
    processing_fingerprint = batch_processing_fingerprint(
        source_fingerprint=file_fingerprint,
        input_mode=input_mode.value,
        trusted_through=trusted_through,
        start_date=start_date,
        end_date=end_date,
        analysis_version=ANALYSIS_VERSION,
    )
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
        log_event(
            LOGGER,
            logging.INFO,
            "ingestion.batch_already_completed",
            batch_id=completed_log.batch_id,
            input_filename=source_name,
            source_row_count=completed_log.source_row_count or 0,
        )
        return ProcessingSummary(
            batch_id=completed_log.batch_id,
            input_filename=source_name,
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

    # The identifier is allocated before analysis so every rejected conflict
    # can be attached to the same auditable batch that reports its count.
    batch_id = uuid4().hex
    started_at = datetime.now(timezone.utc)
    rows = adapter.read_rows(
        source,
        start_date=start_date,
        end_date=end_date,
    )
    # Validation belongs to the application boundary so custom adapters cannot
    # bypass the same source-evidence contract enforced by the Excel adapter.
    validate_input_rows(rows)
    log_event(
        LOGGER,
        logging.INFO,
        "ingestion.batch_started",
        batch_id=batch_id,
        input_filename=source_name,
        input_mode=input_mode.value,
        source_row_count=len(rows),
    )
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
    prefetched_extractions = prefetch_model_extractions(
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
    prefetched_repeat_reviews = prefetch_repeat_reviews(
        payloads=repeat_payloads,
        client=llm_client,
        workers=model_workers,
        cache_path=extraction_cache_path,
        progress_callback=progress_callback,
    )

    new_trajectories: list[CallTrajectory] = []
    conflict_records: list[IngestionConflict] = []
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
            log_event(
                LOGGER,
                logging.WARNING,
                "ingestion.row_rejected",
                batch_id=batch_id,
                row_position=position,
                reason="required_field_invalid",
            )
            if progress_callback is not None:
                progress_callback(position, total_calls, "输入字段无效")
            continue
        record_fingerprint = _source_record_fingerprint(call)
        existing = existing_by_business.get(call.business_id)
        if existing is not None:
            if existing.source_record_fingerprint not in {None, record_fingerprint}:
                conflicts += 1
                conflict_records.append(
                    IngestionConflict(
                        batch_id=batch_id,
                        business_id=call.business_id,
                        source_filename=source_name,
                        existing_record_fingerprint=existing.source_record_fingerprint,
                        incoming_record_fingerprint=record_fingerprint,
                        detected_at=datetime.now(timezone.utc),
                    )
                )
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
            failure_type = type(cause).__name__ if cause else type(exc).__name__
            failure_reasons[failure_type] += 1
            consecutive_model_failures += 1
            log_event(
                LOGGER,
                logging.WARNING,
                "ingestion.row_failed",
                batch_id=batch_id,
                row_position=position,
                stage="model_analysis",
                failure_type=failure_type,
            )
            if progress_callback is not None:
                progress_callback(position, total_calls, "模型分析失败")
            if consecutive_model_failures >= 3:
                log_event(
                    LOGGER,
                    logging.ERROR,
                    "ingestion.batch_circuit_opened",
                    batch_id=batch_id,
                    consecutive_model_failures=consecutive_model_failures,
                )
                raise RuntimeError(
                    "连续 3 条模型分析失败，已熔断；本文件没有写入数据库"
                ) from exc
            continue
        except Exception as exc:
            failed += 1
            failure_type = type(exc).__name__
            failure_reasons[failure_type] += 1
            log_event(
                LOGGER,
                logging.WARNING,
                "ingestion.row_failed",
                batch_id=batch_id,
                row_position=position,
                stage="row_processing",
                failure_type=failure_type,
            )
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
            source_filename=source_name,
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
        update_log = UpdateLog(
            batch_id=batch_id,
            data_date=date_range,
            input_filename=source_name,
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
        session.add(update_log)
        # No ORM relationship is needed for read paths, so make the foreign-key
        # ordering explicit: the parent batch must exist before conflict rows.
        # The surrounding transaction still commits or rolls back as one unit.
        session.flush()
        session.add_all(conflict_records)

    status = "completed" if failed == 0 else "completed_with_errors"
    log_event(
        LOGGER,
        logging.INFO,
        "ingestion.batch_completed",
        batch_id=batch_id,
        input_filename=source_name,
        status=status,
        new_call_count=len(new_trajectories),
        skipped_count=skipped,
        conflict_count=conflicts,
        failed_count=failed,
        updated_profile_count=len(affected_hashes),
    )
    return ProcessingSummary(
        batch_id=batch_id,
        input_filename=source_name,
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

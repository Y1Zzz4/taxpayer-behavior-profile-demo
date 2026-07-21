"""SQLAlchemy models for the three core database tables."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class CallerProfile(Base):
    __tablename__ = "caller_profiles"

    phone_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    phone_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    caller_type: Mapped[str | None] = mapped_column(String(20))
    enterprise_identity: Mapped[str | None] = mapped_column(String(20))
    proficiency_score: Mapped[float | None] = mapped_column(Float)
    proficiency_summary: Mapped[str | None] = mapped_column(String(300))
    first_call_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    latest_call_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    total_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repeated_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repeated_issue_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unresolved_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    work_order_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    abnormal_end_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dissatisfaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latest_business_id: Mapped[str | None] = mapped_column(String(128))
    latest_question: Mapped[str | None] = mapped_column(Text)
    latest_father_question: Mapped[str | None] = mapped_column(Text)
    latest_resolved: Mapped[bool | None] = mapped_column(Boolean)
    latest_service_rating: Mapped[str | None] = mapped_column(String(20))
    recent_questions_summary: Mapped[str | None] = mapped_column(Text)
    unresolved_questions_summary: Mapped[str | None] = mapped_column(Text)
    repeated_questions_summary: Mapped[str | None] = mapped_column(Text)
    profile_summary: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class CallTrajectory(Base):
    __tablename__ = "call_trajectories"

    business_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    call_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    registration_time: Mapped[datetime | None] = mapped_column(DateTime)
    call_end_time: Mapped[datetime | None] = mapped_column(DateTime)
    call_time_source: Mapped[str | None] = mapped_column(String(30))
    caller_type: Mapped[str | None] = mapped_column(String(20))
    enterprise_identity: Mapped[str | None] = mapped_column(String(20))
    core_question: Mapped[str | None] = mapped_column(Text)
    father_question: Mapped[str | None] = mapped_column(Text)
    father_question_2: Mapped[str | None] = mapped_column(Text)
    resolved_status: Mapped[bool | None] = mapped_column(Boolean)
    work_order: Mapped[bool | None] = mapped_column(Boolean)
    rule_abnormal_end: Mapped[bool | None] = mapped_column(Boolean)
    model_abnormal_end: Mapped[bool | None] = mapped_column(Boolean)
    waiting_expression: Mapped[bool | None] = mapped_column(Boolean)
    potential_pushback: Mapped[bool | None] = mapped_column(Boolean)
    taxpayer_dissatisfied: Mapped[bool | None] = mapped_column(Boolean)
    contacted_other_department: Mapped[bool | None] = mapped_column(Boolean)
    active_contacted_other_department: Mapped[bool | None] = mapped_column(Boolean)
    contact_target: Mapped[str | None] = mapped_column(String(300))
    natural_qa_turns: Mapped[int | None] = mapped_column(Integer)
    core_question_turns: Mapped[int | None] = mapped_column(Integer)
    effective_qa_turns: Mapped[int | None] = mapped_column(Integer)
    effective_qa_content: Mapped[str | None] = mapped_column(Text)
    proficiency_score: Mapped[float | None] = mapped_column(Float)
    proficiency_summary: Mapped[str | None] = mapped_column(String(300))
    service_rating: Mapped[str | None] = mapped_column(String(20))
    service_summary: Mapped[str | None] = mapped_column(String(300))
    is_repeated_call: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    previous_call_time: Mapped[datetime | None] = mapped_column(DateTime)
    call_interval: Mapped[int | None] = mapped_column(Integer)
    historical_call_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_repeated_issue: Mapped[bool | None] = mapped_column(Boolean)
    matched_previous_business_id: Mapped[str | None] = mapped_column(String(128))
    matched_previous_question: Mapped[str | None] = mapped_column(Text)
    matched_previous_call_time: Mapped[datetime | None] = mapped_column(DateTime)
    previous_issue_resolved: Mapped[bool | None] = mapped_column(Boolean)
    repeat_reason: Mapped[str | None] = mapped_column(String(40))
    repeat_summary: Mapped[str | None] = mapped_column(String(300))
    repeat_candidate_score: Mapped[float | None] = mapped_column(Float)
    repeat_confidence: Mapped[float | None] = mapped_column(Float)
    repeat_review_status: Mapped[str] = mapped_column(
        String(30), default="not_required", nullable=False
    )
    repeat_review_reason: Mapped[str | None] = mapped_column(String(500))
    repeat_review_requested_at: Mapped[datetime | None] = mapped_column(DateTime)
    repeat_review_requested_by: Mapped[str | None] = mapped_column(String(100))
    repeat_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    repeat_reviewed_by: Mapped[str | None] = mapped_column(String(100))
    repeat_label_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    input_mode: Mapped[str | None] = mapped_column(String(30))
    analysis_source: Mapped[str | None] = mapped_column(String(30))
    model_name: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    extraction_version: Mapped[str | None] = mapped_column(String(50))
    enterprise_identity_source: Mapped[str | None] = mapped_column(String(30))
    enterprise_identity_conflict: Mapped[bool | None] = mapped_column(Boolean)
    source_filename: Mapped[str | None] = mapped_column(String(300))
    source_file_fingerprint: Mapped[str | None] = mapped_column(String(64))
    source_record_fingerprint: Mapped[str | None] = mapped_column(String(64))
    analysis_error: Mapped[str | None] = mapped_column(String(500))
    analysis_status: Mapped[str] = mapped_column(String(30), nullable=False)
    analysis_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class UpdateLog(Base):
    __tablename__ = "update_logs"

    batch_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data_date: Mapped[str] = mapped_column(String(40), nullable=False)
    input_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    input_mode: Mapped[str | None] = mapped_column(String(30))
    analysis_version: Mapped[str | None] = mapped_column(String(50))
    source_row_count: Mapped[int | None] = mapped_column(Integer)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflict_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    new_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_phone_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_profile_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repeated_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repeated_issue_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unresolved_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)

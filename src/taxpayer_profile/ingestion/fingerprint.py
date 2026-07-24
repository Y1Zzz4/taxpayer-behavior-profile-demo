"""Canonical record identity for idempotent incremental ingestion."""

from __future__ import annotations

import hashlib
import json
from datetime import date

from taxpayer_profile.normalization import NormalizedCallInput


def batch_processing_fingerprint(
    *,
    source_fingerprint: str,
    input_mode: str,
    analysis_version: str,
    trusted_through: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> str:
    """Identify one file processed under one complete ingestion contract."""

    value = "|".join(
        (
            source_fingerprint,
            input_mode,
            trusted_through.isoformat() if trusted_through else "",
            start_date.isoformat() if start_date else "",
            end_date.isoformat() if end_date else "",
            analysis_version,
        )
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_record_fingerprint(call: NormalizedCallInput) -> str:
    """Hash the imported source facts and explicitly reusable decisions.

    Recomputed model outputs are intentionally excluded. A prompt or model
    upgrade must not make an unchanged source record look like a conflicting
    correction.
    """

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

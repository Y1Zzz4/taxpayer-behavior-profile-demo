"""Read-only history list and detail use cases."""

from __future__ import annotations

from collections.abc import Callable
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from taxpayer_profile.application.web_dto import (
    contact_unresolved,
    history_detail_payload,
    mask_phone,
    wait_pushback,
)
from taxpayer_profile.models import CallerProfile, CallTrajectory
from taxpayer_profile.security import PhoneProtector


class HistoryService:
    """Project paged call histories while preserving phone masking at the boundary."""

    def __init__(self, sessions: Callable[[], Session], protector: PhoneProtector) -> None:
        self._sessions = sessions
        self._protector = protector

    def page(
        self, *, page: object = 1, page_size: object = 10, phone: object | None = None
    ) -> dict[str, object]:
        try:
            page_number = int(page)
            size = int(page_size)
        except (TypeError, ValueError) as exc:
            raise ValueError("分页参数必须是整数") from exc
        if page_number < 1 or not 1 <= size <= 50:
            raise ValueError("分页参数超出允许范围")
        phone_hash = (
            self._protector.hash_phone(phone)
            if phone is not None and str(phone).strip()
            else None
        )
        filters = ((CallTrajectory.phone_hash == phone_hash,) if phone_hash else ())
        with self._sessions() as session:
            total = (
                session.scalar(select(func.count()).select_from(CallTrajectory).where(*filters))
                or 0
            )
            trajectories = session.scalars(
                select(CallTrajectory)
                .where(*filters)
                .order_by(CallTrajectory.call_time.desc(), CallTrajectory.business_id.desc())
                .offset((page_number - 1) * size)
                .limit(size)
            ).all()
            profiles = {
                item.phone_hash: item
                for item in session.scalars(
                    select(CallerProfile).where(
                        CallerProfile.phone_hash.in_(
                            {item.phone_hash for item in trajectories}
                        )
                    )
                ).all()
            }
        items = []
        for item in trajectories:
            masked = "号码不可用"
            profile = profiles.get(item.phone_hash)
            if profile is not None:
                try:
                    masked = mask_phone(
                        self._protector.decrypt_phone(profile.phone_encrypted)
                    )
                except (ValueError, TypeError):
                    pass
            items.append(
                {
                    "masked_phone": masked,
                    "business_id": item.business_id,
                    "call_time": item.call_time,
                    "caller_type": item.caller_type,
                    "enterprise_identity": item.enterprise_identity,
                    "core_question": item.core_question,
                    "question_category": item.topic_category,
                    "secondary_topic": item.secondary_topic,
                    "demand_category": item.demand_category,
                    "registration_unit": item.registration_unit,
                    "resolved": item.resolved_status,
                    "unresolved_reason": item.unresolved_reason,
                    "work_order": item.work_order,
                    "is_repeated_issue": item.is_repeated_issue,
                    "wait_pushback": wait_pushback(item),
                    "taxpayer_dissatisfied": item.taxpayer_dissatisfied,
                    "contact_unresolved": contact_unresolved(item),
                    "analysis_status": item.analysis_status,
                }
            )
        return {
            "page": page_number,
            "page_size": size,
            "total": total,
            "total_pages": ceil(total / size) if total else 0,
            "filtered": phone_hash is not None,
            "items": items,
        }

    def detail(self, business_id: object) -> dict[str, object] | None:
        identifier = str(business_id or "").strip()
        if not identifier:
            raise ValueError("缺少业务编号")
        with self._sessions() as session:
            item = session.get(CallTrajectory, identifier)
        if item is None:
            return None
        masked = "号码不可用"
        if item.raw_phone_encrypted:
            try:
                masked = mask_phone(self._protector.decrypt_phone(item.raw_phone_encrypted))
            except (ValueError, TypeError):
                pass
        return history_detail_payload(item, masked)

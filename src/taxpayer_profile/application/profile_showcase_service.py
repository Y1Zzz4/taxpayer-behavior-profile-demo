"""Read-only profile-showcase catalog and graph use cases."""

from __future__ import annotations

from collections.abc import Callable
from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import Session

from taxpayer_profile.application.web_dto import mask_phone
from taxpayer_profile.application.web_profile_support import (
    HISTORICAL_FACT_DEFINITIONS,
    PROFILE_DIMENSION_TAXONOMY,
    fact_counts,
    mode_payload,
    profile_snapshot,
    recent_five_workday_items,
    showcase_key,
)
from taxpayer_profile.models import CallerProfile, CallTrajectory
from taxpayer_profile.profiling import (
    RECEPTION_MODE_CATALOG,
    RECEPTION_MODE_GROUPS,
    classify_reception_mode,
)
from taxpayer_profile.security import PhoneProtector


class ProfileShowcaseService:
    """Serve anonymized profile graph data without writing simulation data."""

    def __init__(self, sessions: Callable[[], Session], protector: PhoneProtector) -> None:
        self._sessions = sessions
        self._protector = protector

    @cached_property
    def _search_index(self) -> list[dict[str, object]]:
        """Build a lightweight search index without loading every call trajectory."""

        with self._sessions() as session:
            profiles = session.scalars(
                select(CallerProfile).order_by(CallerProfile.latest_call_time.desc())
            ).all()
        entries: list[dict[str, object]] = []
        for index, profile in enumerate(profiles, 1):
            try:
                phone = self._protector.decrypt_phone(profile.phone_encrypted)
                masked = mask_phone(phone)
            except (ValueError, TypeError):
                phone = ""
                masked = "号码不可用"
            entries.append(
                {
                    "index": index,
                    "phone_hash": profile.phone_hash,
                    "profile_key": showcase_key(profile.phone_hash),
                    "masked_phone": masked,
                    "search_text": " ".join(
                        (
                            str(index),
                            str(index).zfill(2),
                            phone,
                            masked,
                            profile.proficiency_level or "",
                            profile.emotion_state or "",
                        )
                    ).lower(),
                    "proficiency_level": profile.proficiency_level,
                    "emotion_state": profile.emotion_state,
                    "latest_call_time": profile.latest_call_time,
                }
            )
        return entries

    @cached_property
    def _key_map(self) -> dict[str, dict[str, object]]:
        return {str(entry["profile_key"]): entry for entry in self._search_index}

    def catalog(self, *, query: object = "", limit: object = 5) -> dict[str, object]:
        normalized_query = str(query or "").strip().lower()
        try:
            result_limit = max(1, min(int(limit), 5))
        except (TypeError, ValueError) as exc:
            raise ValueError("画像检索数量必须是整数") from exc
        candidates = [
            entry
            for entry in self._search_index
            if not normalized_query or normalized_query in str(entry["search_text"])
        ][:result_limit]
        candidate_hashes = [str(entry["phone_hash"]) for entry in candidates]
        trajectories_by_phone: dict[str, list[CallTrajectory]] = {}
        if candidate_hashes:
            with self._sessions() as session:
                trajectories = session.scalars(
                    select(CallTrajectory).where(
                        CallTrajectory.phone_hash.in_(candidate_hashes)
                    )
                ).all()
            for trajectory in trajectories:
                trajectories_by_phone.setdefault(trajectory.phone_hash, []).append(
                    trajectory
                )
        items: list[dict[str, object]] = []
        for entry in candidates:
            phone_hash = str(entry["phone_hash"])
            recent_facts = fact_counts(
                recent_five_workday_items(trajectories_by_phone.get(phone_hash, []))
            )
            mode_result = classify_reception_mode(
                proficiency_level=entry["proficiency_level"],
                emotion_state=entry["emotion_state"],
                wait_pushback_count=recent_facts["wait_pushback"],
                work_order_count=recent_facts["work_order"],
                abnormal_end_count=recent_facts["abnormal_end"],
                contact_unresolved_count=recent_facts["contact_unresolved"],
                dissatisfaction_count=recent_facts["dissatisfaction"],
            )
            items.append(
                {
                    "index": entry["index"],
                    "profile_key": entry["profile_key"],
                    "label": entry["masked_phone"],
                    "masked_phone": entry["masked_phone"],
                    "recommended_mode": mode_result.mode,
                    "recommended_modes": [
                        {"category": component.category, "mode": component.mode}
                        for component in mode_result.components
                    ],
                    "proficiency_level": entry["proficiency_level"],
                    "emotion_state": entry["emotion_state"],
                    "latest_call_time": entry["latest_call_time"],
                }
            )
        modes = [dict(mode) for mode in RECEPTION_MODE_CATALOG]
        relations = [
            {"category": "情绪响应", "source": "不满/等待推诿/对坐席不满", "target": "安抚修复"},
            {"category": "情绪响应", "source": "焦虑", "target": "稳定预期"},
            {"category": "情绪响应", "source": "其余状态", "target": "平稳接待"},
            {"category": "业务应对", "source": "工单/异常中断/联系后未解决", "target": "历史诉求跟进"},
            {"category": "业务应对", "source": "无待衔接事实", "target": "当前诉求确认"},
            {"category": "表达方式", "source": "专业", "target": "结论直述"},
            {"category": "表达方式", "source": "了解", "target": "重点解释"},
            {"category": "表达方式", "source": "小白或证据不足", "target": "通俗引导"},
        ]
        return {
            "items": items,
            "taxonomy": {
                "dimensions": list(PROFILE_DIMENSION_TAXONOMY),
                "historical_facts": list(HISTORICAL_FACT_DEFINITIONS),
                "service_mode_groups": list(RECEPTION_MODE_GROUPS),
                "service_modes": modes,
                "relations": relations,
            },
            "summary": {
                "dimension_count": 3,
                "fact_count": 5,
                "mode_group_count": 3,
                "mode_count": len(RECEPTION_MODE_CATALOG),
                "profile_count": len(self._search_index),
                "returned_count": len(items),
            },
            "methodology": [
                {
                    "title": "单通提取",
                    "description": "提取业务专业度和近期情绪状态；既有分析字段优先复用。",
                },
                {
                    "title": "五日聚合",
                    "description": "按号码汇总最近五个工作日的五项历史服务事实。",
                },
                {
                    "title": "接待方式匹配",
                    "description": "从表达方式、情绪响应和业务应对中各选择一项，组合形成完整接待策略。",
                },
            ],
        }

    def profile(self, *, profile_key: object) -> dict[str, object]:
        key = str(profile_key or "").strip()
        with self._sessions() as session:
            matched = self._key_map.get(key)
            profile = (
                session.get(CallerProfile, str(matched["phone_hash"]))
                if matched is not None
                else None
            )
            if profile is None:
                raise ValueError("未找到对应画像")
            trajectories = session.scalars(
                select(CallTrajectory)
                .where(CallTrajectory.phone_hash == profile.phone_hash)
                .order_by(CallTrajectory.call_time.asc())
            ).all()
        try:
            masked = mask_phone(self._protector.decrypt_phone(profile.phone_encrypted))
        except (ValueError, TypeError):
            masked = "号码不可用"
        facts = fact_counts(recent_five_workday_items(trajectories))
        before_state: dict[str, object] = {
            "proficiency_level": profile.proficiency_level or "暂无法判断",
            "proficiency_basis": profile.proficiency_basis,
            "emotion_state": profile.emotion_state or "暂无法判断",
            "emotion_basis": profile.emotion_basis,
            **facts,
        }
        return {
            "profile_key": key,
            "masked_phone": masked,
            "before": {
                "state": before_state,
                "result": mode_payload(before_state),
                "profile_model": profile_snapshot(before_state),
            },
        }

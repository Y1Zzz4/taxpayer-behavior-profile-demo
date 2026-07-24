"""Application queries and projections used by the service-assistance UI."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from functools import cached_property
from math import ceil
from pathlib import Path
from typing import Callable

from sqlalchemy import func, select

from taxpayer_profile.application.web_dto import (
    abnormal_end as _abnormal,
    contact_unresolved as _contact_unresolved,
    counter_rows as _counter_rows,
    district_unit_label as _district_unit_label,
    history_detail_payload,
    mask_phone as _mask_phone,
    resolution_rows as _resolution_rows,
    resolution_state as _resolution_state,
    secondary_labels_for_topic as _secondary_labels_for_topic,
    segmented_rows as _segmented_rows,
    split_labels as _split_labels,
    wait_pushback as _wait_pushback,
)
from taxpayer_profile.auth import AuthService
from taxpayer_profile.config import Settings
from taxpayer_profile.database import create_schema, make_engine, make_session_factory
from taxpayer_profile.llm_client import OpenAICompatibleClient
from taxpayer_profile.models import CallerProfile, CallTrajectory, UpdateLog
from taxpayer_profile.profiling import (
    RECEPTION_MODE_CATALOG,
    RECEPTION_MODE_GROUPS,
    classify_reception_mode,
)
from taxpayer_profile.query import query_profile
from taxpayer_profile.realtime_advice import (
    AdviceClient,
    build_fallback_advice,
    generate_realtime_advice,
)
from taxpayer_profile.security import PhoneProtector
from taxpayer_profile.service_calendar import recent_service_days

REALTIME_ADVICE_TIMEOUT_SECONDS = 25.0

PROFILE_DIMENSION_TAXONOMY = (
    {
        "id": "proficiency",
        "name": "业务专业度",
        "description": "根据业务表达、术语理解和办理节点认知，调整解释深度。",
        "categories": ("专业", "了解", "小白"),
        "unknown": "暂无法判断",
    },
    {
        "id": "emotion",
        "name": "近期情绪状态",
        "description": "只描述文本中可观察到的近期表达，不评价性格或心理。",
        "categories": ("平稳", "焦虑", "不满"),
        "unknown": "暂无法判断",
    },
    {
        "id": "facts",
        "name": "历史服务事实",
        "description": "按该号码最近五个工作日的明确字段组合形成，可同时命中多项。",
        "categories": (
            "历史工单",
            "联系后未解决",
            "异常中断",
            "等待推诿",
            "对坐席不满",
        ),
        "unknown": "近五个工作日未命中",
    },
)

HISTORICAL_FACT_DEFINITIONS = (
    {"id": "work_order", "label": "历史工单", "rule": "是否工单 = 是"},
    {
        "id": "contact_unresolved",
        "label": "联系相关人员后仍未解决",
        "rule": "联系相关人员或部门 = 是，且坐席是否解决纳税人问题 = 否",
    },
    {
        "id": "abnormal_end",
        "label": "异常中断",
        "rule": "采用原始分析的最终非正常中断字段；新增数据按同口径分析",
    },
    {
        "id": "wait_pushback",
        "label": "等待推诿",
        "rule": "存在让纳税人等待表述 = 是，且坐席存在潜在推诿行为 = 是",
    },
    {
        "id": "dissatisfaction",
        "label": "对坐席不满",
        "rule": "纳税人是否对当前坐席或本通热线存在不满 = 是",
    },
)


def _showcase_key(phone_hash: str) -> str:
    return hashlib.sha256(f"profile-showcase:{phone_hash}".encode()).hexdigest()[:18]


def _fact_counts(items: list[CallTrajectory]) -> dict[str, int]:
    return {
        "wait_pushback": sum(_wait_pushback(item) for item in items),
        "work_order": sum(item.work_order is True for item in items),
        "abnormal_end": sum(_abnormal(item) for item in items),
        "contact_unresolved": sum(_contact_unresolved(item) for item in items),
        "dissatisfaction": sum(item.taxpayer_dissatisfied is True for item in items),
    }


def _recent_five_workday_items(
    items: list[CallTrajectory],
) -> list[CallTrajectory]:
    if not items:
        return []
    anchor = max(item.call_time.date() for item in items)
    workdays = set(recent_service_days(anchor, count=5))
    return [item for item in items if item.call_time.date() in workdays]


def _mode_from_state(state: dict[str, object]):
    return classify_reception_mode(
        proficiency_level=str(state.get("proficiency_level") or "暂无法判断"),
        emotion_state=str(state.get("emotion_state") or "暂无法判断"),
        wait_pushback_count=int(state.get("wait_pushback") or 0),
        work_order_count=int(state.get("work_order") or 0),
        abnormal_end_count=int(state.get("abnormal_end") or 0),
        contact_unresolved_count=int(state.get("contact_unresolved") or 0),
        dissatisfaction_count=int(state.get("dissatisfaction") or 0),
    )


def _mode_payload(state: dict[str, object]) -> dict[str, object]:
    mode = _mode_from_state(state)
    return {
        "service_mode": mode.mode,
        "mode_id": mode.mode_id,
        "mode_components": [
            {
                "category_id": component.category_id,
                "category": component.category,
                "mode_id": component.mode_id,
                "mode": component.mode,
                "basis": component.basis,
                "focus": component.focus,
                "communication": component.communication,
                "avoid": component.avoid,
            }
            for component in mode.components
        ],
        "strategy_reason": mode.basis,
        "service_suggestion": mode.focus,
        "communication": mode.communication,
        "avoid": mode.avoid,
        "matched_facts": list(mode.matched_facts),
    }


def _profile_snapshot(state: dict[str, object]) -> dict[str, object]:
    facts = []
    fact_keys = (
        ("wait_pushback", "等待推诿"),
        ("work_order", "历史工单"),
        ("abnormal_end", "异常中断"),
        ("contact_unresolved", "联系后未解决"),
        ("dissatisfaction", "对坐席不满"),
    )
    for key, label in fact_keys:
        if int(state.get(key) or 0):
            facts.append(label)
    items = [
        {
            "id": "proficiency",
            "name": "业务专业度",
            "value": str(state.get("proficiency_level") or "暂无法判断"),
            "values": [str(state.get("proficiency_level") or "暂无法判断")],
            "basis": str(state.get("proficiency_basis") or "可用证据不足。"),
        },
        {
            "id": "emotion",
            "name": "近期情绪状态",
            "value": str(state.get("emotion_state") or "暂无法判断"),
            "values": [str(state.get("emotion_state") or "暂无法判断")],
            "basis": str(state.get("emotion_basis") or "可用证据不足。"),
        },
        {
            "id": "facts",
            "name": "历史服务事实",
            "value": "、".join(facts) if facts else "近五个工作日未命中",
            "values": facts or ["近五个工作日未命中"],
            "basis": "历史事实按最近五个工作日的明确字段组合计算。",
        },
    ]
    mode = _mode_payload(state)
    return {
        "items": items,
        "signature": " / ".join(str(item["value"]) for item in items),
        "active_category_count": sum(len(item["values"]) for item in items),
        "service_mode": mode["service_mode"],
        "service_actions": [],
    }


@dataclass
class ProfileAdviceService:
    """Phone-level profile lookup and realtime reception advice."""

    database_path: Path
    protector: PhoneProtector
    settings: Settings
    advice_client_factory: Callable[[], AdviceClient | None] | None = None

    def lookup_profile(self, phone: object) -> dict[str, object] | None:
        return query_profile(
            phone=phone,
            database_path=self.database_path,
            protector=self.protector,
        )

    def _advice_client(self) -> AdviceClient | None:
        if self.advice_client_factory is not None:
            return self.advice_client_factory()
        if not self.settings.llm_configured:
            return None
        return OpenAICompatibleClient(
            self.settings.llm_base_url,  # type: ignore[arg-type]
            self.settings.llm_api_key,  # type: ignore[arg-type]
            self.settings.llm_model,  # type: ignore[arg-type]
            timeout_seconds=REALTIME_ADVICE_TIMEOUT_SECONDS,
            max_attempts=1,
        )

    def generate_advice(self, phone: object) -> dict[str, object]:
        profile = self.lookup_profile(phone)
        if profile is None:
            empty_mode = _mode_payload(
                {
                    "proficiency_level": "暂无法判断",
                    "emotion_state": "暂无法判断",
                }
            )
            empty_context = {
                "profile_summary": "该号码暂无历史来电记录。",
                "proficiency_level": "暂无法判断",
                "emotion_state": "暂无法判断",
                "recommended_mode": empty_mode["service_mode"],
                "recommended_modes": empty_mode["mode_components"],
                "mode_basis": empty_mode["strategy_reason"],
                "mode_guidance": {
                    "focus": empty_mode["service_suggestion"],
                    "communication": empty_mode["communication"],
                    "avoid": empty_mode["avoid"],
                    "components": empty_mode["mode_components"],
                },
                "statistics": {},
                "recent_five_workdays": {},
                "recent_trajectories": [],
            }
            return {
                "found": False,
                "advice": build_fallback_advice(
                    empty_context, fallback_reason="profile_not_found"
                ),
            }
        return {
            "found": True,
            "advice": generate_realtime_advice(
                profile["agent_context"],  # type: ignore[arg-type]
                self._advice_client(),
            ),
        }


@dataclass
class DemoService:
    """Stable façade composing the UI's focused application services."""

    database_path: Path
    protector: PhoneProtector
    settings: Settings
    advice_client_factory: Callable[[], AdviceClient | None] | None = None

    @cached_property
    def _sessions(self):  # type: ignore[no-untyped-def]
        engine = make_engine(self.database_path)
        create_schema(engine)
        return make_session_factory(engine)

    @cached_property
    def auth(self) -> AuthService:
        return AuthService(self._sessions)

    @cached_property
    def profile_advice(self) -> ProfileAdviceService:
        return ProfileAdviceService(
            database_path=self.database_path,
            protector=self.protector,
            settings=self.settings,
            advice_client_factory=self.advice_client_factory,
        )

    def initialize_auth(self) -> None:
        self.auth.ensure_default_users(
            admin_username=self.settings.default_admin_username,
            admin_password=self.settings.default_admin_password,
            agent_username=self.settings.default_agent_username,
            agent_password=self.settings.default_agent_password,
        )

    def lookup_profile(self, phone: object) -> dict[str, object] | None:
        return self.profile_advice.lookup_profile(phone)

    def generate_advice(self, phone: object) -> dict[str, object]:
        return self.profile_advice.generate_advice(phone)

    def dashboard_summary(self) -> dict[str, object]:
        with self._sessions() as session:
            profiles = session.scalars(select(CallerProfile)).all()
            trajectories = session.scalars(select(CallTrajectory)).all()
            latest_update = session.scalar(
                select(UpdateLog).order_by(UpdateLog.started_at.desc()).limit(1)
            )

        known_resolution = [
            item.resolved_status
            for item in trajectories
            if item.resolved_status is not None
        ]
        daily_calls = Counter(item.call_time.date().isoformat() for item in trajectories)
        invalid_caller_types = [
            item.business_id
            for item in trajectories
            if item.caller_type not in {"企业", "个人"}
        ]
        if invalid_caller_types:
            # Do not silently normalize a broken invariant into a dashboard
            # category. Legacy data must be repaired with the explicit
            # backfill command before it can be presented as current data.
            raise RuntimeError("数据库存在未完成咨询主体修复的来电记录")
        caller_types = Counter(item.caller_type for item in trajectories)
        topics: Counter[str] = Counter()
        demands: Counter[str] = Counter()
        topic_resolution: dict[str, Counter[str]] = {}
        secondary_topics: dict[str, Counter[str]] = {}
        secondary_resolution: dict[str, dict[str, Counter[str]]] = {}
        demand_resolution: dict[str, Counter[str]] = {}
        registration_units: dict[str, Counter[str]] = {}
        for item in trajectories:
            state = _resolution_state(item)
            primary_labels = _split_labels(
                item.topic_category, fallback="暂未分类"
            )
            secondaries = _split_labels(
                item.secondary_topic, fallback="二级专题待识别"
            )
            for topic in primary_labels:
                topics[topic] += 1
                topic_resolution.setdefault(topic, Counter())[state] += 1
                for secondary in _secondary_labels_for_topic(
                    topic, primary_labels, secondaries
                ):
                    secondary_topics.setdefault(topic, Counter())[secondary] += 1
                    secondary_resolution.setdefault(topic, {}).setdefault(
                        secondary, Counter()
                    )[state] += 1
            labels = _split_labels(item.demand_category, fallback="暂未分类")
            for label in labels:
                demands[label] += 1
                demand_resolution.setdefault(label, Counter())[state] += 1
            unit = _district_unit_label(item.registration_unit)
            registration_units.setdefault(unit, Counter())["total"] += 1
            registration_units[unit][state] += 1

        sorted_dates = sorted(daily_calls)
        trend_dates = []
        if sorted_dates:
            latest_date = max(item.call_time.date() for item in trajectories)
            trend_dates = recent_service_days(latest_date, count=14)
        facts = _fact_counts(trajectories)
        fact_rows = [
            {
                **definition,
                "value": facts[str(definition["id"])],
                "share": round(
                    facts[str(definition["id"])] * 100 / len(trajectories), 1
                )
                if trajectories
                else 0,
            }
            for definition in HISTORICAL_FACT_DEFINITIONS
        ]
        return {
            "overview": {
                "total_profiles": len(profiles),
                "total_calls": len(trajectories),
                "work_orders": facts["work_order"],
                "resolved_rate": (
                    round(
                        100
                        * sum(value is True for value in known_resolution)
                        / len(known_resolution),
                        1,
                    )
                    if known_resolution
                    else None
                ),
                "data_date_range": (
                    f"{sorted_dates[0]} 至 {sorted_dates[-1]}" if sorted_dates else None
                ),
            },
            "daily_calls": [
                {
                    "date": value.isoformat(),
                    "label": value.strftime("%m-%d"),
                    "value": daily_calls[value.isoformat()],
                }
                for value in trend_dates
            ],
            "caller_types": _counter_rows(caller_types),
            "resolution_status": _resolution_rows(trajectories),
            "personal_resolution": _resolution_rows(
                [item for item in trajectories if item.caller_type == "个人"]
            ),
            "enterprise_resolution": _resolution_rows(
                [item for item in trajectories if item.caller_type == "企业"]
            ),
            "question_categories": [
                {
                    **row,
                    "children": _segmented_rows(
                        secondary_topics.get(str(row["label"]), Counter()),
                        secondary_resolution.get(str(row["label"]), {}),
                        limit=5,
                    ),
                }
                for row in _segmented_rows(
                    topics, topic_resolution, limit=5, exclude_other=True
                )
            ],
            "demand_categories": _segmented_rows(
                demands, demand_resolution, limit=5, exclude_other=True
            ),
            "registration_unit_resolution": [
                {
                    "label": label,
                    "total": counts["total"],
                    "resolved": counts["resolved"],
                    "unresolved": counts["unresolved"],
                    "unknown": counts["unknown"],
                    "resolved_rate": (
                        round(
                            counts["resolved"]
                            * 100
                            / (counts["resolved"] + counts["unresolved"]),
                            1,
                        )
                        if counts["resolved"] + counts["unresolved"]
                        else None
                    ),
                }
                for label, counts in sorted(
                    registration_units.items(),
                    key=lambda item: (-item[1]["total"], item[0]),
                )
            ],
            "historical_facts": fact_rows,
            # Compatibility keys retained while the page moves to the simpler layout.
            "service_signals": [
                {"label": row["label"], "value": row["value"]} for row in fact_rows
            ],
            "latest_update": (
                {
                    "data_date": latest_update.data_date,
                    "input_filename": latest_update.input_filename,
                    "status": latest_update.status,
                    "finished_at": latest_update.finished_at,
                    "new_call_count": latest_update.new_call_count,
                    "new_phone_count": latest_update.new_phone_count,
                    "failed_count": latest_update.failed_count,
                    "summary": latest_update.summary,
                }
                if latest_update is not None
                else None
            ),
        }

    @cached_property
    def _showcase_search_index(self) -> list[dict[str, object]]:
        """Build a lightweight search index without loading every call trajectory."""

        with self._sessions() as session:
            profiles = session.scalars(
                select(CallerProfile).order_by(CallerProfile.latest_call_time.desc())
            ).all()
        entries: list[dict[str, object]] = []
        for index, profile in enumerate(profiles, 1):
            try:
                phone = self.protector.decrypt_phone(profile.phone_encrypted)
                masked = _mask_phone(phone)
            except (ValueError, TypeError):
                phone = ""
                masked = "号码不可用"
            entries.append(
                {
                    "index": index,
                    "phone_hash": profile.phone_hash,
                    "profile_key": _showcase_key(profile.phone_hash),
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
    def _showcase_key_map(self) -> dict[str, dict[str, object]]:
        return {
            str(entry["profile_key"]): entry
            for entry in self._showcase_search_index
        }

    def profile_showcase_catalog(
        self, *, query: object = "", limit: object = 5
    ) -> dict[str, object]:
        normalized_query = str(query or "").strip().lower()
        try:
            result_limit = max(1, min(int(limit), 5))
        except (TypeError, ValueError) as exc:
            raise ValueError("画像检索数量必须是整数") from exc
        index = self._showcase_search_index
        candidates = [
            entry
            for entry in index
            if not normalized_query
            or normalized_query in str(entry["search_text"])
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
            recent_facts = _fact_counts(
                _recent_five_workday_items(
                    trajectories_by_phone.get(phone_hash, [])
                )
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
                        {
                            "category": component.category,
                            "mode": component.mode,
                        }
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
                "profile_count": len(index),
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

    def profile_showcase(self, *, profile_key: object) -> dict[str, object]:
        key = str(profile_key or "").strip()
        with self._sessions() as session:
            matched = self._showcase_key_map.get(key)
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
            masked = _mask_phone(self.protector.decrypt_phone(profile.phone_encrypted))
        except (ValueError, TypeError):
            masked = "号码不可用"
        # The database profile already aggregates the recent labels. Facts here are
        # intentionally transparent counts for the demonstration.
        facts = _fact_counts(_recent_five_workday_items(trajectories))
        before_state: dict[str, object] = {
            "proficiency_level": profile.proficiency_level or "暂无法判断",
            "proficiency_basis": profile.proficiency_basis,
            "emotion_state": profile.emotion_state or "暂无法判断",
            "emotion_basis": profile.emotion_basis,
            **facts,
        }
        before = _mode_payload(before_state)
        before_model = _profile_snapshot(before_state)
        return {
            "profile_key": key,
            "masked_phone": masked,
            "before": {"state": before_state, "result": before, "profile_model": before_model},
        }

    def history_page(self, *, page: object = 1, page_size: object = 10, phone: object | None = None) -> dict[str, object]:
        try:
            page_number = int(page)
            size = int(page_size)
        except (TypeError, ValueError) as exc:
            raise ValueError("分页参数必须是整数") from exc
        if page_number < 1 or not 1 <= size <= 50:
            raise ValueError("分页参数超出允许范围")
        phone_hash = None
        if phone is not None and str(phone).strip():
            phone_hash = self.protector.hash_phone(phone)
        filters = ((CallTrajectory.phone_hash == phone_hash,) if phone_hash else ())
        with self._sessions() as session:
            total = session.scalar(select(func.count()).select_from(CallTrajectory).where(*filters)) or 0
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
                        CallerProfile.phone_hash.in_({item.phone_hash for item in trajectories})
                    )
                ).all()
            }
        items = []
        for item in trajectories:
            masked = "号码不可用"
            profile = profiles.get(item.phone_hash)
            if profile is not None:
                try:
                    masked = _mask_phone(self.protector.decrypt_phone(profile.phone_encrypted))
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
                    "wait_pushback": _wait_pushback(item),
                    "taxpayer_dissatisfied": item.taxpayer_dissatisfied,
                    "contact_unresolved": _contact_unresolved(item),
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

    def history_detail(self, business_id: object) -> dict[str, object] | None:
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
                masked = _mask_phone(self.protector.decrypt_phone(item.raw_phone_encrypted))
            except (ValueError, TypeError):
                pass
        return history_detail_payload(item, masked)

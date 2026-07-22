"""Local HTTP application for the simulated 12366 inbound-call demo."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from functools import cached_property
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import ceil
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from sqlalchemy import func, select

from taxpayer_profile.auth import AuthService, user_payload
from taxpayer_profile.config import PROJECT_ROOT, Settings
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

WEB_ROOT = PROJECT_ROOT / "web"
MAX_REQUEST_BYTES = 16_384
REALTIME_ADVICE_TIMEOUT_SECONDS = 25.0
SESSION_COOKIE = "tp_session"

SHOWCASE_SCENARIOS = (
    {
        "id": "baseline",
        "label": "当前结果",
        "description": "只回放现有近五个工作日证据。",
    },
    {
        "id": "followup_signal",
        "label": "新增跟进信号",
        "description": "模拟新增一通联系相关部门后仍未解决的来电。",
    },
    {
        "id": "professional_stable",
        "label": "专业且平稳",
        "description": "模拟近期表达呈现专业、平稳且没有服务修复或事项跟进信号。",
    },
    {
        "id": "service_dissatisfaction",
        "label": "新增不满信号",
        "description": "模拟来电人表达明确不满。",
    },
)

PROFILE_DIMENSION_TAXONOMY = (
    {
        "id": "proficiency",
        "name": "业务熟悉度",
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
            "等待推诿",
            "历史工单",
            "异常中断",
            "联系后未解决",
            "对坐席不满",
        ),
        "unknown": "近五个工作日未命中",
    },
)

HISTORICAL_FACT_DEFINITIONS = (
    {
        "id": "wait_pushback",
        "label": "等待推诿",
        "rule": "存在让纳税人等待表述 = 是，且坐席存在潜在推诿行为 = 是",
    },
    {"id": "work_order", "label": "历史工单", "rule": "是否工单 = 是"},
    {
        "id": "abnormal_end",
        "label": "异常中断",
        "rule": "采用原始分析的最终非正常中断字段；新增数据按同口径分析",
    },
    {
        "id": "contact_unresolved",
        "label": "联系后未解决",
        "rule": "联系相关人员或部门 = 是，且坐席是否解决纳税人问题 = 否",
    },
    {
        "id": "dissatisfaction",
        "label": "对坐席不满",
        "rule": "纳税人是否对当前坐席或本通热线存在不满 = 是",
    },
)


def _showcase_key(phone_hash: str) -> str:
    return hashlib.sha256(f"profile-showcase:{phone_hash}".encode()).hexdigest()[:18]


def _abnormal(item: CallTrajectory) -> bool:
    return item.rule_abnormal_end is True or item.model_abnormal_end is True


def _wait_pushback(item: CallTrajectory) -> bool:
    return item.waiting_expression is True and item.potential_pushback is True


def _contact_unresolved(item: CallTrajectory) -> bool:
    return item.contacted_other_department is True and item.resolved_status is False


def _counter_rows(counter: Counter[str], limit: int | None = None) -> list[dict[str, object]]:
    return [{"label": label, "value": value} for label, value in counter.most_common(limit)]


def _segmented_rows(
    counter: Counter[str], resolution: dict[str, Counter[str]], *, limit: int = 5
) -> list[dict[str, object]]:
    return [
        {
            "label": label,
            "value": value,
            "resolved": resolution[label]["resolved"],
            "unresolved": resolution[label]["unresolved"],
            "unknown": resolution[label]["unknown"],
            "share": round(value * 100 / sum(counter.values()), 1) if counter else 0,
        }
        for label, value in counter.most_common(limit)
    ]


def _mask_phone(phone: str) -> str:
    if len(phone) >= 8:
        return f"{phone[:3]}{'*' * (len(phone) - 7)}{phone[-4:]}"
    if len(phone) >= 5:
        return f"{phone[:2]}{'*' * (len(phone) - 4)}{phone[-2:]}"
    if len(phone) >= 3:
        return f"{phone[0]}{'*' * (len(phone) - 2)}{phone[-1:]}"
    return "*" * len(phone)


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
    workdays = set()
    current = anchor
    while len(workdays) < 5:
        if current.weekday() < 5:
            workdays.add(current)
        current -= timedelta(days=1)
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
            "name": "业务熟悉度",
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
class DemoService:
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

    def initialize_auth(self) -> None:
        self.auth.ensure_default_users(
            admin_username=self.settings.default_admin_username,
            admin_password=self.settings.default_admin_password,
            agent_username=self.settings.default_agent_username,
            agent_password=self.settings.default_agent_password,
        )

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
        caller_types = Counter(item.caller_type or "暂未识别" for item in profiles)
        resolution_status = Counter(
            "已直接解决"
            if item.resolved_status is True
            else "未直接解决"
            if item.resolved_status is False
            else "状态待判定"
            for item in trajectories
        )
        topics: Counter[str] = Counter()
        demands: Counter[str] = Counter()
        topic_resolution: dict[str, Counter[str]] = {}
        demand_resolution: dict[str, Counter[str]] = {}
        for item in trajectories:
            state = (
                "resolved"
                if item.resolved_status is True
                else "unresolved"
                if item.resolved_status is False
                else "unknown"
            )
            topic = item.topic_category or "暂未分类"
            topics[topic] += 1
            topic_resolution.setdefault(topic, Counter())[state] += 1
            labels = [
                part.strip()
                for part in (item.demand_category or "暂未分类")
                .replace("，", ",")
                .split(",")
                if part.strip()
            ]
            for label in labels:
                demands[label] += 1
                demand_resolution.setdefault(label, Counter())[state] += 1

        sorted_dates = sorted(daily_calls)
        trend_dates = []
        if sorted_dates:
            latest_date = max(item.call_time.date() for item in trajectories)
            trend_dates = [latest_date - timedelta(days=offset) for offset in range(13, -1, -1)]
        facts = _fact_counts(trajectories)
        fact_rows = [
            {
                **definition,
                "value": facts[str(definition["id"])],
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
            "resolution_status": _counter_rows(resolution_status),
            "question_categories": _segmented_rows(
                topics, topic_resolution, limit=5
            ),
            "demand_categories": _segmented_rows(
                demands, demand_resolution, limit=5
            ),
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

    def profile_showcase_catalog(self) -> dict[str, object]:
        with self._sessions() as session:
            profiles = session.scalars(
                select(CallerProfile).order_by(CallerProfile.latest_call_time.desc())
            ).all()
            trajectories_by_phone: dict[str, list[CallTrajectory]] = {}
            for item in session.scalars(select(CallTrajectory)).all():
                trajectories_by_phone.setdefault(item.phone_hash, []).append(item)
        items: list[dict[str, object]] = []
        mode_counts: Counter[str] = Counter()
        for profile in profiles:
            try:
                masked = _mask_phone(self.protector.decrypt_phone(profile.phone_encrypted))
            except (ValueError, TypeError):
                masked = "号码不可用"
            # Catalog labels are intentionally short; detail remains in the result panel.
            recent_facts = _fact_counts(
                _recent_five_workday_items(
                    trajectories_by_phone.get(profile.phone_hash, [])
                )
            )
            mode_result = classify_reception_mode(
                proficiency_level=profile.proficiency_level,
                emotion_state=profile.emotion_state,
                wait_pushback_count=recent_facts["wait_pushback"],
                work_order_count=recent_facts["work_order"],
                abnormal_end_count=recent_facts["abnormal_end"],
                contact_unresolved_count=recent_facts["contact_unresolved"],
                dissatisfaction_count=recent_facts["dissatisfaction"],
            )
            for component in mode_result.components:
                mode_counts[component.mode] += 1
            items.append(
                {
                    "profile_key": _showcase_key(profile.phone_hash),
                    "label": masked,
                    "masked_phone": masked,
                    "recommended_mode": mode_result.mode,
                    "recommended_modes": [
                        {
                            "category": component.category,
                            "mode": component.mode,
                        }
                        for component in mode_result.components
                    ],
                    "proficiency_level": profile.proficiency_level,
                    "emotion_state": profile.emotion_state,
                    "latest_call_time": profile.latest_call_time,
                }
            )
        modes = [
            {
                **mode,
                "current_count": mode_counts[str(mode["label"])],
            }
            for mode in RECEPTION_MODE_CATALOG
        ]
        relations = [
            {"category": "情绪响应", "source": "不满/等待推诿/对坐席不满", "target": "安抚修复"},
            {"category": "情绪响应", "source": "焦虑", "target": "稳定预期"},
            {"category": "情绪响应", "source": "其余状态", "target": "平稳接待"},
            {"category": "事项承接", "source": "工单/异常中断/联系后未解决", "target": "历史跟进"},
            {"category": "事项承接", "source": "无待衔接事实", "target": "诉求确认"},
            {"category": "表达方式", "source": "专业", "target": "结论直述"},
            {"category": "表达方式", "source": "了解", "target": "重点解释"},
            {"category": "表达方式", "source": "小白或证据不足", "target": "通俗引导"},
        ]
        return {
            "items": items,
            "scenarios": list(SHOWCASE_SCENARIOS),
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
                "profile_count": len(items),
            },
            "methodology": [
                {
                    "title": "单通提取",
                    "description": "提取业务熟悉度和近期情绪；既有分析字段优先复用。",
                },
                {
                    "title": "五日聚合",
                    "description": "按号码汇总最近五个工作日的五项历史服务事实。",
                },
                {
                    "title": "接待方式匹配",
                    "description": "从情绪响应、事项承接和表达方式中各选择一项，组合形成完整接待策略。",
                },
            ],
        }

    def profile_showcase(self, *, profile_key: object, scenario: object = "baseline") -> dict[str, object]:
        key = str(profile_key or "").strip()
        scenario_id = str(scenario or "baseline").strip()
        scenario_map = {str(item["id"]): item for item in SHOWCASE_SCENARIOS}
        if scenario_id not in scenario_map:
            raise ValueError("不支持该推演场景")
        with self._sessions() as session:
            profile = next(
                (
                    item
                    for item in session.scalars(select(CallerProfile)).all()
                    if _showcase_key(item.phone_hash) == key
                ),
                None,
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
        after_state = dict(before_state)
        event = "当前仅回放数据库中的画像字段和历史事实。"
        if scenario_id == "followup_signal":
            after_state["contact_unresolved"] = int(after_state["contact_unresolved"]) + 1
            event = "新增一通联系相关人员或部门后仍未解决的来电。"
        elif scenario_id == "professional_stable":
            after_state.update(
                {
                    "proficiency_level": "专业",
                    "proficiency_basis": "能够准确描述业务条件和办理节点。",
                    "emotion_state": "平稳",
                    "emotion_basis": "表达有序，没有明显担忧或负面评价。",
                    "wait_pushback": 0,
                    "work_order": 0,
                    "abnormal_end": 0,
                    "contact_unresolved": 0,
                    "dissatisfaction": 0,
                }
            )
            event = "模拟近期窗口只保留专业、平稳表达，且服务修复和事项跟进事实均已移出窗口。"
        elif scenario_id == "service_dissatisfaction":
            after_state["emotion_state"] = "不满"
            after_state["emotion_basis"] = "表达中出现明确负面评价。"
            after_state["dissatisfaction"] = int(after_state["dissatisfaction"]) + 1
            event = "新增一通对本通服务表达明确不满的来电。"
        before = _mode_payload(before_state)
        after = _mode_payload(after_state)
        before_model = _profile_snapshot(before_state)
        after_model = _profile_snapshot(after_state)
        fields = (
            ("proficiency_level", "业务熟悉度"),
            ("emotion_state", "近期情绪状态"),
            ("wait_pushback", "等待推诿"),
            ("work_order", "历史工单"),
            ("abnormal_end", "异常中断"),
            ("contact_unresolved", "联系后未解决"),
            ("dissatisfaction", "对坐席不满"),
        )
        changes = [
            {
                "field": label,
                "before": before_state[key],
                "after": after_state[key],
                "changed": before_state[key] != after_state[key],
            }
            for key, label in fields
        ]
        before_components = {
            str(item["category_id"]): item for item in before["mode_components"]  # type: ignore[index]
        }
        after_components = {
            str(item["category_id"]): item for item in after["mode_components"]  # type: ignore[index]
        }
        for category_id, category_label in (
            ("emotion_response", "情绪响应"),
            ("matter_continuity", "事项承接"),
            ("information_delivery", "表达方式"),
        ):
            before_mode = str(before_components[category_id]["mode"])
            after_mode = str(after_components[category_id]["mode"])
            changes.append(
                {
                    "field": category_label,
                    "before": before_mode,
                    "after": after_mode,
                    "changed": before_mode != after_mode,
                }
            )
        changes.append(
            {
                "field": "组合接待策略",
                "before": before["service_mode"],
                "after": after["service_mode"],
                "changed": before["service_mode"] != after["service_mode"],
            }
        )
        timeline = [
            {
                "index": index,
                "business_id": item.business_id,
                "call_time": item.call_time,
                "question": item.core_question or "咨询事项未形成明确记录",
                "resolved": item.resolved_status,
                "contributions": [
                    text
                    for condition, text in (
                        (item.proficiency_level is not None, f"熟悉度：{item.proficiency_level}"),
                        (item.emotion_state is not None, f"情绪：{item.emotion_state}"),
                        (_wait_pushback(item), "等待推诿"),
                        (item.work_order is True, "历史工单"),
                        (_abnormal(item), "异常中断"),
                        (_contact_unresolved(item), "联系后未解决"),
                        (item.taxpayer_dissatisfied is True, "对坐席不满"),
                    )
                    if condition
                ]
                or ["保留为基础来电事实"],
            }
            for index, item in enumerate(trajectories, 1)
        ]
        return {
            "profile_key": key,
            "masked_phone": masked,
            "scenario": {**scenario_map[scenario_id], "event": event},
            "profile": {
                "caller_type": profile.caller_type,
                "enterprise_identity": profile.enterprise_identity,
                "latest_question": profile.latest_question,
                "topic_category": profile.latest_topic_category,
                "demand_category": profile.latest_demand_category,
                "proficiency_level": profile.proficiency_level,
                "proficiency_basis": profile.proficiency_basis,
                "emotion_state": profile.emotion_state,
                "emotion_basis": profile.emotion_basis,
                "first_call_time": profile.first_call_time,
                "latest_call_time": profile.latest_call_time,
            },
            "timeline": timeline,
            "before": {"state": before_state, "result": before, "profile_model": before_model},
            "after": {"state": after_state, "result": after, "profile_model": after_model},
            "changes": changes,
            "disclaimer": "本次为情景推演，结果不写入正式画像或来电记录，仅供演示参考。",
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
        return {
            "original": {
                "business_id": item.business_id,
                "transcript": item.raw_transcript,
                "registration_time": item.registration_time,
                "call_start_time": item.raw_call_start_time,
                "call_end_time": item.call_end_time,
                "agent_id": item.agent_id,
                "agent_name": item.agent_name,
                "business_content": item.business_content,
                "answer_content": item.answer_content,
                "recording_path": item.recording_path,
                "registration_unit": item.registration_unit,
                "handling_method": item.handling_method,
                "business_category": item.business_category,
                "masked_phone": masked,
                "satisfaction": item.satisfaction,
                "call_serial_number": item.call_serial_number,
            },
            "extracted": {
                "caller_type": item.caller_type,
                "detailed_subject": item.enterprise_identity,
                "core_question": item.core_question,
                "topic_category": item.topic_category,
                "demand_category": item.demand_category,
                "resolved": item.resolved_status,
                "unresolved_reason": item.unresolved_reason,
                "work_order": item.work_order,
                "wait_pushback": _wait_pushback(item),
                "abnormal_end": _abnormal(item),
                "contact_unresolved": _contact_unresolved(item),
                "taxpayer_dissatisfied": item.taxpayer_dissatisfied,
                "proficiency_level": item.proficiency_level,
                "proficiency_basis": item.proficiency_basis,
                "emotion_state": item.emotion_state,
                "emotion_basis": item.emotion_basis,
                "is_repeated_issue": item.is_repeated_issue,
                "repeat_reason": item.repeat_reason,
                "matched_previous_question": item.matched_previous_question,
                "previous_issue_resolved": item.previous_issue_resolved,
                "repeat_summary": item.repeat_summary,
                "repeat_review_status": item.repeat_review_status,
                "analysis_status": item.analysis_status,
            },
        }


def _handler_factory(service: DemoService) -> type[BaseHTTPRequestHandler]:
    class DemoHandler(BaseHTTPRequestHandler):
        server_version = "TaxpayerProfileDemo/0.2"

        def _json(
            self,
            status: int,
            payload: object,
            *,
            headers: dict[str, str] | None = None,
        ) -> None:
            content = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(content)

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"error": message})

        def _read_json(self) -> dict[str, object]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("请求长度无效") from exc
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("请求内容为空或过大")
            try:
                body = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError("请求必须是有效 JSON") from exc
            if not isinstance(body, dict):
                raise ValueError("请求内容必须是 JSON 对象")
            return body

        def _token(self) -> str | None:
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            item = cookie.get(SESSION_COOKIE)
            return item.value if item else None

        def _require_user(self, *, admin: bool = False):  # type: ignore[no-untyped-def]
            user = service.auth.authenticate(self._token())
            if user is None:
                self._error(401, "请先登录")
                return None
            if admin and user.role != "admin":
                self._error(403, "当前账号无权访问该功能")
                return None
            return user

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/":
                content = (WEB_ROOT / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)
                return
            if path == "/app.js":
                content = (WEB_ROOT / "app.js").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)
                return
            if path == "/api/auth/me":
                user = self._require_user()
                if user is not None:
                    self._json(200, {"user": user_payload(user)})
                return
            admin_only = path in {"/api/showcase/catalog", "/api/users"}
            if self._require_user(admin=admin_only) is None:
                return
            if path == "/api/dashboard":
                self._json(200, service.dashboard_summary())
            elif path == "/api/showcase/catalog":
                self._json(200, service.profile_showcase_catalog())
            elif path == "/api/users":
                self._json(200, {"items": service.auth.list_users()})
            else:
                self._error(404, "接口不存在")

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                body = self._read_json()
            except ValueError as exc:
                self._error(400, str(exc))
                return
            if path == "/api/auth/login":
                try:
                    token, user = service.auth.login(body.get("username"), body.get("password"))
                    cookie = (
                        f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800"
                    )
                    self._json(200, {"user": user}, headers={"Set-Cookie": cookie})
                except ValueError as exc:
                    self._error(401, str(exc))
                return
            if path == "/api/auth/logout":
                service.auth.logout(self._token())
                self._json(
                    200,
                    {"ok": True},
                    headers={
                        "Set-Cookie": f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
                    },
                )
                return
            admin_only = path in {"/api/showcase", "/api/users/create", "/api/users/update"}
            if self._require_user(admin=admin_only) is None:
                return
            try:
                if path == "/api/profile":
                    if body.get("phone") is None:
                        raise ValueError("缺少来电号码")
                    profile = service.lookup_profile(body["phone"])
                    self._json(200, {"found": profile is not None, "profile": profile})
                elif path == "/api/advice":
                    if body.get("phone") is None:
                        raise ValueError("缺少来电号码")
                    self._json(200, service.generate_advice(body["phone"]))
                elif path == "/api/history":
                    self._json(
                        200,
                        service.history_page(
                            page=body.get("page", 1),
                            page_size=body.get("page_size", 10),
                            phone=body.get("phone"),
                        ),
                    )
                elif path == "/api/history/detail":
                    detail = service.history_detail(body.get("business_id"))
                    self._json(200, {"found": detail is not None, "detail": detail})
                elif path == "/api/showcase":
                    self._json(
                        200,
                        service.profile_showcase(
                            profile_key=body.get("profile_key"),
                            scenario=body.get("scenario", "baseline"),
                        ),
                    )
                elif path == "/api/users/create":
                    self._json(
                        200,
                        {
                            "user": service.auth.create_user(
                                username=body.get("username"),
                                display_name=body.get("display_name"),
                                password=body.get("password"),
                                role=body.get("role"),
                            )
                        },
                    )
                elif path == "/api/users/update":
                    self._json(
                        200,
                        {
                            "user": service.auth.update_user(
                                user_id=body.get("user_id"),
                                display_name=body.get("display_name"),
                                role=body.get("role"),
                                is_active=body.get("is_active"),
                                password=body.get("password"),
                            )
                        },
                    )
                else:
                    self._error(404, "接口不存在")
            except ValueError as exc:
                self._error(400, str(exc))

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    return DemoHandler


def run_server(*, service: DemoService, host: str = "127.0.0.1", port: int = 8000) -> None:
    service.initialize_auth()
    server = ThreadingHTTPServer((host, port), _handler_factory(service))
    print(f"12366坐席服务辅助系统已启动：http://{host}:{port}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

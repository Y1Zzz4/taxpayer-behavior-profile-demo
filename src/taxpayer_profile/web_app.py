"""Small localhost-only HTTP application for the simulated inbound-call demo."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from functools import cached_property
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import ceil
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from sqlalchemy import func, select

from taxpayer_profile.config import PROJECT_ROOT, Settings
from taxpayer_profile.database import make_engine, make_session_factory
from taxpayer_profile.llm_client import OpenAICompatibleClient
from taxpayer_profile.models import CallerProfile, CallTrajectory, UpdateLog
from taxpayer_profile.profiling import build_service_strategy, classify_service_profile
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
SHOWCASE_SCENARIOS = (
    {
        "id": "baseline",
        "label": "当前画像",
        "description": "只展示现有历史证据，不增加模拟记录。",
    },
    {
        "id": "repeat_unresolved",
        "label": "同类事项再次未解决",
        "description": "模拟同一问题再次来电，且本通仍未直接解决。",
    },
    {
        "id": "resolved_closure",
        "label": "历史事项完成闭环",
        "description": "模拟来电核验进度后，将一项历史待办确认解决。",
    },
    {
        "id": "service_dissatisfaction",
        "label": "新增服务不满信号",
        "description": "模拟来电人对等待或转交过程表达不满。",
    },
)

PROFILE_DIMENSION_TAXONOMY = (
    {
        "id": "subject",
        "name": "咨询主体",
        "description": "识别最近一次来电代表的主体及企业细化角色，用于调整核验内容，不用于身份定性。",
        "categories": (
            "个人",
            "企业·法定代表人",
            "企业·财务负责人",
            "企业·办税人员",
            "企业·其他",
            "企业·细化主体待识别",
            "主体待识别",
        ),
    },
    {
        "id": "demand",
        "name": "诉求形态",
        "description": "复用单通来电需求类别，可同时保留两个彼此独立的诉求标签。",
        "categories": (
            "政策咨询类",
            "操作辅导类",
            "工单/拉起类",
            "涉税查询类",
            "系统异常类",
            "投诉举报类",
            "意见建议类",
            "其他类",
        ),
    },
    {
        "id": "continuity",
        "name": "互动连续性",
        "description": "区分首次接触、一般复访、持续咨询和已经核实的同题重复。",
        "categories": ("初次接触", "常规复访", "持续咨询", "同题重复"),
    },
    {
        "id": "progress",
        "name": "事项进展",
        "description": "描述最近事项是否闭环、待跟进或已经进入工单流转。",
        "categories": ("最近已闭环", "事项待跟进", "工单推进", "状态待确认"),
    },
    {
        "id": "capability",
        "name": "业务认知",
        "description": "依据历史表达和问答证据调整术语密度及步骤粒度。",
        "categories": ("引导辅助", "常规理解", "熟练自主", "证据不足"),
    },
    {
        "id": "experience",
        "name": "服务体验",
        "description": "只评价历史服务过程信号，用于判断是否需要提高节点透明度。",
        "categories": ("服务平稳", "过程关注", "信任修复"),
    },
)

SERVICE_ACTION_CATALOG = (
    {"id": "clarify", "label": "诉求开放式确认", "description": "先确认本次实际诉求，不把历史事项直接当成本次问题。"},
    {"id": "history", "label": "历史节点衔接", "description": "经来电人确认后，从已知处理节点继续，减少重复复述。"},
    {"id": "progress", "label": "进度与责任透明", "description": "说明当前状态、下一责任方、预计时间和查询入口。"},
    {"id": "difference", "label": "新旧差异核对", "description": "重点核对材料、系统提示、处理进度或理解上的新增变化。"},
    {"id": "conclusion", "label": "结论条件优先", "description": "先给结论框架、适用条件和例外，再补充必要步骤。"},
    {"id": "guided", "label": "通俗分步引导", "description": "降低术语密度，一次给出少量步骤并等待操作结果。"},
    {"id": "confirm", "label": "关键节点复述确认", "description": "在材料、页面或办理节点处确认理解和当前结果。"},
    {"id": "transfer", "label": "等待与转交说明", "description": "发生等待或转交时主动说明原因、责任边界和预计时长。"},
    {"id": "closure", "label": "闭环与查询方式", "description": "结束前确认是否解决，并说明仍待处理事项及后续查询方式。"},
    {"id": "separate", "label": "新旧事项分流", "description": "明确区分延续事项和本次新问题，避免误归为重复咨询。"},
)

SERVICE_MODE_CATALOG = (
    {
        "id": "trust",
        "label": "信任修复与节点透明型",
        "definition": "用于出现服务体验风险的情形，优先复述诉求，并透明说明等待、转交和责任节点。",
        "rule": "主画像为服务关注型，或任一画像同时出现潜在推诿、两次及以上异常结束。",
        "profiles": ("服务关注型", "事项跟进型", "持续咨询型", "常规服务型"),
        "actions": ("clarify", "progress", "transfer", "closure"),
    },
    {
        "id": "progress",
        "label": "事项进度核验与闭环型",
        "definition": "用于未直接解决、工单或多次未闭环事项，先核验当前节点，再明确下一责任方和时间点。",
        "rule": "主画像为事项跟进型，即未直接解决次数大于0或历史工单次数大于0。",
        "profiles": ("事项跟进型",),
        "actions": ("clarify", "history", "progress", "closure", "separate"),
    },
    {
        "id": "difference",
        "label": "重复问题差异核对型",
        "definition": "用于同题重复咨询，重点确认本次相较前次新增的材料、状态、提示或理解差异。",
        "rule": "主画像为持续咨询型，且已确认同类重复诉求次数大于0。",
        "profiles": ("持续咨询型",),
        "actions": ("clarify", "history", "difference", "closure", "separate"),
    },
    {
        "id": "history",
        "label": "历史上下文衔接型",
        "definition": "用于一般复访或多次来电，先判断是否延续历史事项，再从已确认节点继续。",
        "rule": "主画像为持续咨询型或常规服务型，且没有更优先事项，熟练度处于常规区间或证据不足。",
        "profiles": ("持续咨询型", "常规服务型"),
        "actions": ("clarify", "history", "separate", "closure"),
    },
    {
        "id": "conclusion",
        "label": "结论条件优先型",
        "definition": "用于业务认知较高的来电人，先给结论、适用条件与例外，再补充关键操作节点。",
        "rule": "主画像为持续咨询型或常规服务型，且历史业务熟练度大于等于8分。",
        "profiles": ("持续咨询型", "常规服务型"),
        "actions": ("clarify", "conclusion", "confirm", "closure"),
    },
    {
        "id": "guided",
        "label": "分步操作陪伴确认型",
        "definition": "用于业务认知较低的来电人，降低术语密度，分段给出操作并逐节点确认。",
        "rule": "主画像为持续咨询型或常规服务型，且历史业务熟练度小于等于4分。",
        "profiles": ("持续咨询型", "常规服务型"),
        "actions": ("clarify", "guided", "confirm", "closure"),
    },
    {
        "id": "initial",
        "label": "首次诉求澄清与标准引导型",
        "definition": "用于历史证据较少或认知程度待判断的情形，先完整澄清诉求，再按标准流程引导。",
        "rule": "主画像为常规服务型，且无稳定历史衔接关系和明确熟练度分层。",
        "profiles": ("常规服务型",),
        "actions": ("clarify", "confirm", "closure"),
    },
)

COMPOSITE_PROFILE_CATALOG = (
    {
        "type": "服务关注型",
        "priority": 1,
        "definition": "历史服务体验需要优先修复，接待时首先保证等待、转交和责任节点透明。",
        "rule": "历史对本通热线服务不满次数 > 0",
        "dimensions": ("experience", "progress", "continuity"),
        "modes": ("trust",),
    },
    {
        "type": "事项跟进型",
        "priority": 2,
        "definition": "历史存在未直接解决事项或工单，应优先核对进度并形成可追踪的后续闭环。",
        "rule": "未直接解决次数 > 0，或历史工单次数 > 0",
        "dimensions": ("progress", "continuity", "demand"),
        "modes": ("trust", "progress"),
    },
    {
        "type": "持续咨询型",
        "priority": 3,
        "definition": "存在稳定复访或同题重复特征，应复用历史上下文并重点核对本次新增变化。",
        "rule": "同类重复诉求次数 > 0，或累计来电次数 ≥ 3",
        "dimensions": ("continuity", "progress", "demand"),
        "modes": ("trust", "difference", "history", "conclusion", "guided"),
    },
    {
        "type": "常规服务型",
        "priority": 4,
        "definition": "未出现更高优先级服务信号；具体采用结论优先、分步陪伴或首次引导，由业务认知与互动连续性共同决定。",
        "rule": "P1—P3均未命中时作为兜底分类",
        "dimensions": ("continuity", "progress", "capability"),
        "modes": ("trust", "history", "conclusion", "guided", "initial"),
    },
)


def _showcase_key(phone_hash: str) -> str:
    return hashlib.sha256(f"profile-showcase:{phone_hash}".encode()).hexdigest()[:18]


def _strategy_payload(
    *,
    profile: CallerProfile,
    trajectories: list[CallTrajectory],
    state: dict[str, int],
    latest_resolved: bool | None,
) -> dict[str, object]:
    question = profile.latest_question or "最近咨询事项"
    unresolved_questions = [
        item.core_question or item.topic_category or "历史未解决事项"
        for item in trajectories
        if item.resolved_status is False
    ]
    classification = classify_service_profile(
        total_calls=state["total_calls"],
        repeated_issues=state["repeated_issues"],
        unresolved=state["unresolved"],
        work_orders=state["work_orders"],
        dissatisfaction=state["dissatisfaction"],
        proficiency_score=profile.proficiency_score,
    )
    strategy = build_service_strategy(
        total_calls=state["total_calls"],
        repeated_issues=state["repeated_issues"],
        unresolved=state["unresolved"],
        work_orders=state["work_orders"],
        abnormal_ends=state["abnormal_ends"],
        dissatisfaction=state["dissatisfaction"],
        has_pushback=any(item.potential_pushback is True for item in trajectories),
        latest_resolved=latest_resolved,
        proficiency_score=profile.proficiency_score,
        latest_question=question,
        recent_questions=[
            item.core_question
            for item in reversed(trajectories)
            if item.core_question
        ][:3],
        unresolved_questions=unresolved_questions[:3],
    )
    return {
        "profile_type": classification.profile_type,
        "profile_basis": classification.basis,
        "attention_level": strategy.attention_level,
        "service_mode": strategy.recommended_mode,
        "strategy_reason": strategy.reason,
        "service_suggestion": strategy.suggestion,
    }


def _profile_dimension_snapshot(
    *,
    profile: CallerProfile,
    trajectories: list[CallTrajectory],
    state: dict[str, int],
    latest_resolved: bool | None,
) -> dict[str, object]:
    """Build concurrent, explainable profile dimensions from stored facts."""

    if profile.caller_type == "企业":
        identity = profile.enterprise_identity
        subject_value = (
            f"企业·{identity}"
            if identity in {"法定代表人", "财务负责人", "办税人员", "其他"}
            else "企业·细化主体待识别"
        )
        subject_basis = f"最近咨询主体为企业，细化主体为{identity or '无法判断'}。"
    elif profile.caller_type == "个人":
        subject_value = "个人"
        subject_basis = "最近咨询主体识别为个人。"
    else:
        subject_value = "主体待识别"
        subject_basis = "现有记录不足以稳定判断最近咨询主体。"

    demand_values = [
        item.strip()
        for item in (profile.latest_demand_category or "").replace("，", ",").split(",")
        if item.strip()
    ][:2]
    demand_basis = (
        f"最近一通来电的需求类别为{'、'.join(demand_values)}。"
        if demand_values
        else "最近一通来电尚未形成稳定需求类别。"
    )

    if state["repeated_issues"] > 0:
        continuity_value = "同题重复"
        continuity_basis = (
            f"累计来电{state['total_calls']}次，其中已确认同一问题重复咨询"
            f"{state['repeated_issues']}次。"
        )
    elif state["total_calls"] >= 3:
        continuity_value = "持续咨询"
        continuity_basis = f"累计来电{state['total_calls']}次，已形成持续复访特征。"
    elif state["total_calls"] <= 1:
        continuity_value = "初次接触"
        continuity_basis = "当前仅有一次历史来电，连续性证据仍较少。"
    else:
        continuity_value = "常规复访"
        continuity_basis = (
            f"累计来电{state['total_calls']}次，但尚未确认同一问题重复咨询。"
        )

    if state["work_orders"] > 0:
        progress_value = "工单推进"
        progress_basis = f"历史存在{state['work_orders']}次工单记录，需要核验承办节点。"
    elif state["unresolved"] > 0 or latest_resolved is False:
        progress_value = "事项待跟进"
        progress_basis = f"历史存在{state['unresolved']}次未直接解决记录。"
    elif latest_resolved is True:
        progress_value = "最近已闭环"
        progress_basis = "最近一次来电记录显示事项已直接解决。"
    else:
        progress_value = "状态待确认"
        progress_basis = "最近事项的解决状态尚无充分证据。"

    score = profile.proficiency_score
    if score is None:
        capability_value = "证据不足"
        capability_basis = "历史表达与问答证据不足，暂不预设理解程度。"
    elif score < 5:
        capability_value = "引导辅助"
        capability_basis = f"历史业务熟练度为{score:.1f}/10，适合通俗分步引导。"
    elif score >= 8:
        capability_value = "熟练自主"
        capability_basis = f"历史业务熟练度为{score:.1f}/10，可压缩基础概念铺垫。"
    else:
        capability_value = "常规理解"
        capability_basis = f"历史业务熟练度为{score:.1f}/10，建议按反馈调整解释深度。"

    has_pushback = any(item.potential_pushback is True for item in trajectories)
    if state["dissatisfaction"] > 0:
        experience_value = "信任修复"
        experience_basis = (
            f"历史存在{state['dissatisfaction']}次对本通热线服务不满记录，"
            "应提高等待与转交透明度。"
        )
    elif state["abnormal_ends"] > 0 or has_pushback:
        experience_value = "过程关注"
        signals = []
        if state["abnormal_ends"] > 0:
            signals.append(f"{state['abnormal_ends']}次异常结束")
        if has_pushback:
            signals.append("潜在推诿信号")
        experience_basis = f"历史存在{'、'.join(signals)}，需要加强过程确认。"
    else:
        experience_value = "服务平稳"
        experience_basis = "历史暂未出现明确服务不满、推诿或异常结束信号。"

    values_by_id: dict[str, tuple[list[str], str]] = {
        "subject": ([subject_value], subject_basis),
        "demand": (demand_values or ["需求待识别"], demand_basis),
        "continuity": ([continuity_value], continuity_basis),
        "progress": ([progress_value], progress_basis),
        "capability": ([capability_value], capability_basis),
        "experience": ([experience_value], experience_basis),
    }
    items = []
    for dimension in PROFILE_DIMENSION_TAXONOMY:
        values, basis = values_by_id[str(dimension["id"])]
        items.append(
            {
                "id": dimension["id"],
                "name": dimension["name"],
                "values": values,
                "value": "、".join(values),
                "basis": basis,
            }
        )

    unresolved_questions = [
        item.core_question or item.topic_category or "历史未解决事项"
        for item in trajectories
        if item.resolved_status is False
    ]
    strategy = build_service_strategy(
        total_calls=state["total_calls"],
        repeated_issues=state["repeated_issues"],
        unresolved=state["unresolved"],
        work_orders=state["work_orders"],
        abnormal_ends=state["abnormal_ends"],
        dissatisfaction=state["dissatisfaction"],
        has_pushback=has_pushback,
        latest_resolved=latest_resolved,
        proficiency_score=profile.proficiency_score,
        latest_question=profile.latest_question,
        recent_questions=[
            item.core_question for item in reversed(trajectories) if item.core_question
        ][:3],
        unresolved_questions=unresolved_questions[:3],
    )
    selected_mode = next(
        (
            item
            for item in SERVICE_MODE_CATALOG
            if item["label"] == strategy.recommended_mode
        ),
        None,
    )
    unique_action_ids = (
        list(selected_mode["actions"])
        if selected_mode
        else ["clarify", "confirm", "closure"]
    )
    action_by_id = {str(item["id"]): item for item in SERVICE_ACTION_CATALOG}

    return {
        "items": items,
        "signature": " / ".join(item["value"] for item in items),
        "active_category_count": sum(len(item["values"]) for item in items),
        "service_mode": strategy.recommended_mode,
        "service_actions": [action_by_id[action_id] for action_id in unique_action_ids],
    }


@dataclass
class DemoService:
    database_path: Path
    protector: PhoneProtector
    settings: Settings
    advice_client_factory: Callable[[], AdviceClient | None] | None = None

    @cached_property
    def _sessions(self):  # type: ignore[no-untyped-def]
        return make_session_factory(make_engine(self.database_path))

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
            empty_context = {
                "profile_summary": "该号码暂无历史来电记录。",
                "proficiency_score": None,
                "proficiency_summary": "无法判断",
                "statistics": {},
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
        """Return aggregate, non-identifying statistics for the overview page."""

        with self._sessions() as session:
            profiles = session.scalars(select(CallerProfile)).all()
            trajectories = session.scalars(select(CallTrajectory)).all()
            latest_update = session.scalar(
                select(UpdateLog).order_by(UpdateLog.started_at.desc()).limit(1)
            )

        proficiency = [
            item.proficiency_score
            for item in profiles
            if item.proficiency_score is not None
        ]
        known_resolution = [
            item.resolved_status
            for item in trajectories
            if item.resolved_status is not None
        ]
        daily_calls = Counter(item.call_time.date().isoformat() for item in trajectories)
        caller_types = Counter(item.caller_type or "暂未识别" for item in profiles)
        service_profile_types = Counter(
            classify_service_profile(
                total_calls=item.total_call_count,
                repeated_issues=item.repeated_issue_count,
                unresolved=item.unresolved_count,
                work_orders=item.work_order_count,
                dissatisfaction=item.dissatisfaction_count,
                proficiency_score=item.proficiency_score,
            ).profile_type
            for item in profiles
        )
        service_ratings = Counter(
            item.service_rating or "暂未评价" for item in trajectories
        )
        question_categories: Counter[str] = Counter()
        demand_categories: Counter[str] = Counter()
        question_resolution: dict[str, Counter[str]] = {}
        demand_resolution: dict[str, Counter[str]] = {}
        for item in trajectories:
            resolution_key = (
                "resolved"
                if item.resolved_status is True
                else "unresolved"
                if item.resolved_status is False
                else "unknown"
            )
            topic_label = item.topic_category or "暂未分类"
            question_categories[topic_label] += 1
            question_resolution.setdefault(topic_label, Counter())[resolution_key] += 1
            labels = (
                [part.strip() for part in item.demand_category.split(",")]
                if item.demand_category
                else ["暂未分类"]
            )
            for label in labels:
                if not label:
                    continue
                demand_categories[label] += 1
                demand_resolution.setdefault(label, Counter())[resolution_key] += 1
        resolution_status = Counter(
            "已直接解决"
            if item.resolved_status is True
            else "未直接解决"
            if item.resolved_status is False
            else "状态待判定"
            for item in trajectories
        )
        proficiency_bands = Counter(
            "较熟练（8-10分）"
            if item.proficiency_score is not None and item.proficiency_score >= 8
            else "一般（5-7.9分）"
            if item.proficiency_score is not None and item.proficiency_score >= 5
            else "需更多引导（0-4.9分）"
            if item.proficiency_score is not None
            else "暂未评估"
            for item in profiles
        )
        if daily_calls:
            latest_date = max(item.call_time.date() for item in trajectories)
            first_date = min(item.call_time.date() for item in trajectories)
            trend_start = latest_date - timedelta(days=13)
            trend_dates = [
                trend_start + timedelta(days=offset)
                for offset in range((latest_date - trend_start).days + 1)
            ]
        else:
            first_date = latest_date = None
            trend_dates = []

        return {
            "overview": {
                "total_profiles": len(profiles),
                "total_calls": len(trajectories),
                "unresolved_calls": sum(
                    item.resolved_status is False for item in trajectories
                ),
                "work_orders": sum(item.work_order is True for item in trajectories),
                "repeated_calls": sum(item.is_repeated_call for item in trajectories),
                "repeated_issues": sum(
                    item.is_repeated_issue is True for item in trajectories
                ),
                "profiles_with_proficiency": len(proficiency),
                "average_proficiency": (
                    round(sum(proficiency) / len(proficiency), 1)
                    if proficiency
                    else None
                ),
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
                "average_calls_per_profile": (
                    round(len(trajectories) / len(profiles), 1) if profiles else 0
                ),
                "question_category_count": len(question_categories),
                "data_date_range": (
                    f"{first_date.isoformat()} 至 {latest_date.isoformat()}"
                    if first_date is not None and latest_date is not None
                    else None
                ),
            },
            "daily_calls": [
                {
                    "date": date.isoformat(),
                    "label": date.strftime("%m-%d"),
                    "value": daily_calls.get(date.isoformat(), 0),
                }
                for date in trend_dates
            ],
            "caller_types": _counter_rows(caller_types),
            "service_profile_types": _counter_rows(service_profile_types),
            "resolution_status": _counter_rows(resolution_status),
            "service_ratings": _counter_rows(service_ratings),
            "question_categories": _segmented_counter_rows(
                question_categories, question_resolution, limit=8
            ),
            "demand_categories": _segmented_counter_rows(
                demand_categories, demand_resolution
            ),
            "proficiency_bands": _counter_rows(proficiency_bands),
            "service_signals": [
                {
                    "label": "未直接解决",
                    "value": sum(
                        item.resolved_status is False for item in trajectories
                    ),
                },
                {
                    "label": "已形成工单",
                    "value": sum(item.work_order is True for item in trajectories),
                },
                {
                    "label": "重复来电",
                    "value": sum(item.is_repeated_call for item in trajectories),
                },
                {
                    "label": "重复事项",
                    "value": sum(
                        item.is_repeated_issue is True for item in trajectories
                    ),
                },
                {
                    "label": "服务需关注",
                    "value": sum(
                        item.taxpayer_dissatisfied is True
                        or item.service_rating == "需关注"
                        for item in trajectories
                    ),
                },
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
        """List masked example profiles for the read-only profile showcase."""

        with self._sessions() as session:
            profiles = session.scalars(
                select(CallerProfile).order_by(CallerProfile.latest_call_time.desc())
            ).all()

        items: list[dict[str, object]] = []
        for profile in profiles:
            classification = classify_service_profile(
                total_calls=profile.total_call_count,
                repeated_issues=profile.repeated_issue_count,
                unresolved=profile.unresolved_count,
                work_orders=profile.work_order_count,
                dissatisfaction=profile.dissatisfaction_count,
                proficiency_score=profile.proficiency_score,
            )
            try:
                masked_phone = _mask_phone(
                    self.protector.decrypt_phone(profile.phone_encrypted)
                )
            except (ValueError, TypeError):
                masked_phone = "号码信息不可用"
            items.append(
                {
                    "profile_key": _showcase_key(profile.phone_hash),
                    "masked_phone": masked_phone,
                    "profile_type": classification.profile_type,
                    "latest_question": profile.latest_question or "最近咨询事项未记录",
                    "total_calls": profile.total_call_count,
                    "repeated_issues": profile.repeated_issue_count,
                    "unresolved": profile.unresolved_count,
                    "dissatisfaction": profile.dissatisfaction_count,
                    "latest_call_time": profile.latest_call_time,
                    "presentation_priority": (
                        100
                        if not any(
                            (
                                profile.dissatisfaction_count,
                                profile.unresolved_count,
                                profile.repeated_issue_count,
                            )
                        )
                        else 60
                        if profile.unresolved_count and not profile.dissatisfaction_count
                        else 30
                    )
                    + int(bool(profile.latest_question)),
                }
            )
        items.sort(
            key=lambda item: (
                -int(item["presentation_priority"]),
                str(item["latest_call_time"]),
            )
        )
        for item in items:
            item.pop("presentation_priority", None)
        profile_counts = Counter(str(item["profile_type"]) for item in items)
        composite_profiles = [
            {
                **item,
                "current_count": profile_counts.get(str(item["type"]), 0),
            }
            for item in COMPOSITE_PROFILE_CATALOG
        ]
        dimension_category_count = sum(
            len(item["categories"]) for item in PROFILE_DIMENSION_TAXONOMY
        )
        return {
            "items": items,
            "scenarios": list(SHOWCASE_SCENARIOS),
            "taxonomy": {
                "version": "multidimensional-profile-v1",
                "dimension_count": len(PROFILE_DIMENSION_TAXONOMY),
                "dimension_category_count": dimension_category_count,
                "composite_profile_count": len(COMPOSITE_PROFILE_CATALOG),
                "service_mode_count": len(SERVICE_MODE_CATALOG),
                "service_action_count": len(SERVICE_ACTION_CATALOG),
                "dimensions": list(PROFILE_DIMENSION_TAXONOMY),
                "composite_profiles": composite_profiles,
                "service_modes": list(SERVICE_MODE_CATALOG),
                "service_actions": list(SERVICE_ACTION_CATALOG),
            },
            "methodology": [
                "先区分重复来电和同一问题重复咨询，避免仅凭号码频次下结论。",
                "核心问题保持具体到业务场景和实际诉求，再结合历史语义关系判断。",
                "同一号码同时保留咨询主体、诉求形态、互动连续性、事项进展、业务认知和服务体验六个维度。",
                "综合服务画像按服务关注、事项跟进、持续咨询、常规服务四级优先规则归类，不覆盖其他并存标签。",
                "坐席接待模式在主画像之后，继续结合互动连续性和业务认知细分，因此不是简单的一类画像对应一种话术。",
                "画像按新增来电逐次更新，每个结论均保留到单通来电的证据入口。",
            ],
        }

    def profile_showcase(
        self, *, profile_key: object, scenario: object = "baseline"
    ) -> dict[str, object]:
        """Return evidence, profile and a non-persistent incremental simulation."""

        key = str(profile_key or "").strip()
        scenario_id = str(scenario or "baseline").strip()
        scenario_map = {item["id"]: item for item in SHOWCASE_SCENARIOS}
        if not key:
            raise ValueError("缺少画像标识")
        if scenario_id not in scenario_map:
            raise ValueError("不支持该推演场景")

        with self._sessions() as session:
            profiles = session.scalars(select(CallerProfile)).all()
            profile = next(
                (item for item in profiles if _showcase_key(item.phone_hash) == key),
                None,
            )
            if profile is None:
                raise ValueError("未找到对应画像")
            trajectories = list(
                session.scalars(
                    select(CallTrajectory)
                    .where(CallTrajectory.phone_hash == profile.phone_hash)
                    .order_by(
                        CallTrajectory.call_time.asc(),
                        CallTrajectory.business_id.asc(),
                    )
                )
            )

        try:
            masked_phone = _mask_phone(
                self.protector.decrypt_phone(profile.phone_encrypted)
            )
        except (ValueError, TypeError):
            masked_phone = "号码信息不可用"

        state = {
            "total_calls": profile.total_call_count,
            "repeated_calls": profile.repeated_call_count,
            "repeated_issues": profile.repeated_issue_count,
            "unresolved": profile.unresolved_count,
            "work_orders": profile.work_order_count,
            "abnormal_ends": profile.abnormal_end_count,
            "dissatisfaction": profile.dissatisfaction_count,
        }
        before = _strategy_payload(
            profile=profile,
            trajectories=trajectories,
            state=state,
            latest_resolved=profile.latest_resolved,
        )
        before_profile_model = _profile_dimension_snapshot(
            profile=profile,
            trajectories=trajectories,
            state=state,
            latest_resolved=profile.latest_resolved,
        )
        after_state = state.copy()
        after_resolved = profile.latest_resolved
        scenario_event = "当前仅回放数据库中的历史事实。"
        if scenario_id == "repeat_unresolved":
            after_state["total_calls"] += 1
            after_state["repeated_calls"] += 1
            after_state["repeated_issues"] += 1
            after_state["unresolved"] += 1
            after_resolved = False
            scenario_event = "新增一通同类事项来电，经核对仍未直接解决。"
        elif scenario_id == "resolved_closure":
            after_state["total_calls"] += 1
            after_state["repeated_calls"] += 1
            after_state["repeated_issues"] += int(bool(trajectories))
            after_state["unresolved"] = max(0, after_state["unresolved"] - 1)
            after_resolved = True
            scenario_event = "新增一通进度核验来电，并确认一项历史待办已完成闭环。"
        elif scenario_id == "service_dissatisfaction":
            after_state["total_calls"] += 1
            after_state["repeated_calls"] += 1
            after_state["dissatisfaction"] += 1
            after_resolved = None
            scenario_event = "新增一通来电，来电人对等待或转交过程表达不满。"
        after = _strategy_payload(
            profile=profile,
            trajectories=trajectories,
            state=after_state,
            latest_resolved=after_resolved,
        )
        after_profile_model = _profile_dimension_snapshot(
            profile=profile,
            trajectories=trajectories,
            state=after_state,
            latest_resolved=after_resolved,
        )

        daily_counts = Counter(item.call_time.date() for item in trajectories)
        max_single_day = max(daily_counts.values(), default=0)
        rolling_three_day = 0
        if daily_counts:
            first_day = min(daily_counts)
            last_day = max(daily_counts)
            current = first_day
            ordered_days = []
            while current <= last_day:
                ordered_days.append(daily_counts.get(current, 0))
                current += timedelta(days=1)
            rolling_three_day = max(
                sum(ordered_days[max(0, index - 2) : index + 1])
                for index in range(len(ordered_days))
            )

        timeline = []
        for index, item in enumerate(reversed(trajectories), 1):
            contributions = []
            if item.caller_type:
                contributions.append(f"主体识别：{item.caller_type}")
            if item.core_question:
                contributions.append("形成具体咨询主题")
            if item.proficiency_score is not None:
                contributions.append(f"熟练度证据：{item.proficiency_score:.1f}/10")
            if item.resolved_status is False:
                contributions.append("进入未解决事项池")
            if item.is_repeated_issue is True:
                contributions.append("确认同一问题重复咨询")
            elif item.repeat_review_status == "pending_review":
                contributions.append("进入重复问题候选核对")
            if item.taxpayer_dissatisfied is True:
                contributions.append("形成服务关注信号")
            timeline.append(
                {
                    "index": len(trajectories) - index + 1,
                    "business_id": item.business_id,
                    "call_time": item.call_time,
                    "question": item.core_question or "咨询事项未形成明确记录",
                    "topic_category": item.topic_category,
                    "demand_category": item.demand_category,
                    "resolved": item.resolved_status,
                    "unresolved_reason": item.unresolved_reason,
                    "is_repeated_issue": item.is_repeated_issue,
                    "repeat_status": item.repeat_review_status,
                    "repeat_summary": item.repeat_summary,
                    "service_rating": item.service_rating,
                    "contributions": contributions or ["保留为基础来电事实"],
                }
            )

        metric_labels = {
            "total_calls": "累计来电",
            "repeated_issues": "同一问题重复咨询",
            "unresolved": "未直接解决",
            "work_orders": "历史工单",
            "dissatisfaction": "服务不满",
        }
        changes = [
            {
                "field": label,
                "before": state[field],
                "after": after_state[field],
                "changed": state[field] != after_state[field],
            }
            for field, label in metric_labels.items()
        ]
        before_dimensions = {
            str(item["id"]): item
            for item in before_profile_model["items"]  # type: ignore[union-attr]
        }
        after_dimensions = {
            str(item["id"]): item
            for item in after_profile_model["items"]  # type: ignore[union-attr]
        }
        for dimension in PROFILE_DIMENSION_TAXONOMY:
            dimension_id = str(dimension["id"])
            before_value = str(before_dimensions[dimension_id]["value"])
            after_value = str(after_dimensions[dimension_id]["value"])
            changes.append(
                {
                    "field": dimension["name"],
                    "before": before_value,
                    "after": after_value,
                    "changed": before_value != after_value,
                }
            )
        changes.extend(
            [
                {
                    "field": "服务画像",
                    "before": before["profile_type"],
                    "after": after["profile_type"],
                    "changed": before["profile_type"] != after["profile_type"],
                },
                {
                    "field": "推荐服务方式",
                    "before": before["service_mode"],
                    "after": after["service_mode"],
                    "changed": before["service_mode"] != after["service_mode"],
                },
            ]
        )

        return {
            "profile_key": key,
            "masked_phone": masked_phone,
            "scenario": {**scenario_map[scenario_id], "event": scenario_event},
            "profile": {
                "caller_type": profile.caller_type,
                "enterprise_identity": profile.enterprise_identity,
                "latest_question": profile.latest_question,
                "topic_category": profile.latest_topic_category,
                "demand_category": profile.latest_demand_category,
                "registration_unit": profile.latest_registration_unit,
                "proficiency_score": profile.proficiency_score,
                "proficiency_summary": profile.proficiency_summary,
                "latest_service_rating": profile.latest_service_rating,
                "first_call_time": profile.first_call_time,
                "latest_call_time": profile.latest_call_time,
            },
            "rolling_signals": {
                "total_calls": profile.total_call_count,
                "same_direction_count": (
                    min(profile.total_call_count, profile.repeated_issue_count + 1)
                    if profile.repeated_issue_count
                    else 0
                ),
                "repeat_candidates": sum(
                    item.repeat_review_status == "pending_review"
                    for item in trajectories
                ),
                "max_single_day": max_single_day,
                "max_three_days": rolling_three_day,
            },
            "timeline": timeline,
            "before": {
                "state": state,
                "result": before,
                "profile_model": before_profile_model,
            },
            "after": {
                "state": after_state,
                "result": after,
                "profile_model": after_profile_model,
            },
            "changes": changes,
            "disclaimer": "该页面用于演示画像增量逻辑。模拟事件仅在本次页面请求中计算，不写入画像库或来电轨迹。",
        }

    def history_page(
        self, *, page: object = 1, page_size: object = 10, phone: object | None = None
    ) -> dict[str, object]:
        """Return one newest-first page of call trajectories with masked numbers."""

        try:
            page_number = int(page)
            size = int(page_size)
        except (TypeError, ValueError) as exc:
            raise ValueError("分页参数必须是整数") from exc
        if page_number < 1:
            raise ValueError("页码必须大于等于 1")
        if not 1 <= size <= 50:
            raise ValueError("每页条数必须在 1 至 50 之间")

        phone_hash: str | None = None
        if phone is not None and str(phone).strip():
            phone_hash = self.protector.hash_phone(phone)

        filters = (
            (CallTrajectory.phone_hash == phone_hash,) if phone_hash is not None else ()
        )
        with self._sessions() as session:
            total = session.scalar(
                select(func.count()).select_from(CallTrajectory).where(*filters)
            ) or 0
            trajectories = session.scalars(
                select(CallTrajectory)
                .where(*filters)
                .order_by(
                    CallTrajectory.call_time.desc(),
                    CallTrajectory.business_id.desc(),
                )
                .offset((page_number - 1) * size)
                .limit(size)
            ).all()
            hashes = {item.phone_hash for item in trajectories}
            profiles = {
                item.phone_hash: item
                for item in session.scalars(
                    select(CallerProfile).where(CallerProfile.phone_hash.in_(hashes))
                ).all()
            }

        items: list[dict[str, object]] = []
        for item in trajectories:
            profile = profiles.get(item.phone_hash)
            masked_phone = "号码信息不可用"
            if profile is not None:
                try:
                    masked_phone = _mask_phone(
                        self.protector.decrypt_phone(profile.phone_encrypted)
                    )
                except (ValueError, TypeError):
                    pass
            items.append(
                {
                    "masked_phone": masked_phone,
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
                    "is_repeated_call": item.is_repeated_call,
                    "is_repeated_issue": item.is_repeated_issue,
                    "service_rating": item.service_rating,
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
        identifier = str(business_id).strip()
        if not identifier:
            raise ValueError("缺少业务编号")
        with self._sessions() as session:
            item = session.get(CallTrajectory, identifier)
        if item is None:
            return None
        masked_phone = "号码信息不可用"
        if item.raw_phone_encrypted:
            try:
                masked_phone = _mask_phone(
                    self.protector.decrypt_phone(item.raw_phone_encrypted)
                )
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
                "masked_phone": masked_phone,
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
                "proficiency_score": item.proficiency_score,
                "proficiency_summary": item.proficiency_summary,
                "service_effect_rating": item.service_rating,
                "service_effect_summary": item.service_summary,
                "is_repeated_issue": item.is_repeated_issue,
                "repeat_reason": item.repeat_reason,
                "matched_previous_question": item.matched_previous_question,
                "matched_previous_call_time": item.matched_previous_call_time,
                "previous_issue_resolved": item.previous_issue_resolved,
                "repeat_summary": item.repeat_summary,
                "repeat_confidence": item.repeat_confidence,
                "repeat_review_status": item.repeat_review_status,
                "contact_target": item.contact_target,
                "analysis_status": item.analysis_status,
            },
        }


def _counter_rows(
    counter: Counter[str], limit: int | None = None
) -> list[dict[str, object]]:
    rows = counter.most_common(limit)
    return [{"label": label, "value": value} for label, value in rows]


def _segmented_counter_rows(
    counter: Counter[str],
    resolution: dict[str, Counter[str]],
    limit: int | None = None,
) -> list[dict[str, object]]:
    return [
        {
            "label": label,
            "value": value,
            "resolved": resolution[label]["resolved"],
            "unresolved": resolution[label]["unresolved"],
            "unknown": resolution[label]["unknown"],
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


def _handler_factory(service: DemoService) -> type[BaseHTTPRequestHandler]:
    class DemoHandler(BaseHTTPRequestHandler):
        server_version = "TaxpayerProfileDemo/0.1"

        def _json(self, status: int, payload: object) -> None:
            content = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
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

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                content = (WEB_ROOT / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)
                return
            if parsed.path == "/api/dashboard":
                try:
                    self._json(200, service.dashboard_summary())
                except ValueError as exc:
                    self._error(400, str(exc))
                return
            if parsed.path == "/api/showcase/catalog":
                try:
                    self._json(200, service.profile_showcase_catalog())
                except ValueError as exc:
                    self._error(400, str(exc))
                return
            self._error(404, "接口不存在")

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path not in {
                "/api/profile",
                "/api/advice",
                "/api/history",
                "/api/history/detail",
                "/api/showcase",
            }:
                self._error(404, "接口不存在")
                return
            try:
                body = self._read_json()
            except ValueError as exc:
                self._error(400, str(exc))
                return
            try:
                if path == "/api/profile":
                    phone = body.get("phone")
                    if phone is None:
                        raise ValueError("缺少来电号码")
                    profile = service.lookup_profile(phone)
                    self._json(200, {"found": profile is not None, "profile": profile})
                elif path == "/api/advice":
                    phone = body.get("phone")
                    if phone is None:
                        raise ValueError("缺少来电号码")
                    self._json(200, service.generate_advice(phone))
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
                    business_id = body.get("business_id")
                    if business_id is None:
                        raise ValueError("缺少业务编号")
                    detail = service.history_detail(business_id)
                    self._json(200, {"found": detail is not None, "detail": detail})
                else:
                    self._json(
                        200,
                        service.profile_showcase(
                            profile_key=body.get("profile_key"),
                            scenario=body.get("scenario", "baseline"),
                        ),
                    )
            except ValueError as exc:
                self._error(400, str(exc))

        def log_message(self, format: str, *args: object) -> None:
            del format, args
            print(f"{self.address_string()} - {self.command} {urlparse(self.path).path}")

    return DemoHandler


def run_server(
    *, service: DemoService, host: str = "127.0.0.1", port: int = 8000
) -> None:
    server = ThreadingHTTPServer((host, port), _handler_factory(service))
    print(f"坐席服务辅助系统已启动：http://{host}:{port}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

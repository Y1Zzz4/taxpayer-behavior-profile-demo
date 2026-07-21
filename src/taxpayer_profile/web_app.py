"""Small localhost-only HTTP application for the simulated inbound-call demo."""

from __future__ import annotations

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
from taxpayer_profile.query import query_profile
from taxpayer_profile.realtime_advice import (
    AdviceClient,
    build_fallback_advice,
    generate_realtime_advice,
)
from taxpayer_profile.security import PhoneProtector

WEB_ROOT = PROJECT_ROOT / "web"
MAX_REQUEST_BYTES = 16_384


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
            timeout_seconds=12.0,
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
        service_ratings = Counter(
            item.service_rating or "暂未评价" for item in trajectories
        )
        question_categories = Counter(
            item.father_question_2 or item.father_question or "暂未分类"
            for item in trajectories
        )
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
        classified_count = sum(
            bool(item.father_question_2 or item.father_question)
            for item in trajectories
        )
        analysis_completed = sum(
            item.analysis_status == "completed" for item in trajectories
        )
        quality_rows = [
            _quality_row("结构化分析完成度", analysis_completed, len(trajectories)),
            _quality_row("咨询事项分类覆盖", classified_count, len(trajectories)),
            _quality_row("解决状态判定覆盖", len(known_resolution), len(trajectories)),
            _quality_row("画像熟练度评估覆盖", len(proficiency), len(profiles)),
        ]
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
            "resolution_status": _counter_rows(resolution_status),
            "service_ratings": _counter_rows(service_ratings),
            "question_categories": _counter_rows(question_categories, limit=8),
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
            "data_quality": quality_rows,
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
                    "call_time": item.call_time,
                    "caller_type": item.caller_type,
                    "enterprise_identity": item.enterprise_identity,
                    "core_question": item.core_question,
                    "question_category": item.father_question_2
                    or item.father_question,
                    "resolved": item.resolved_status,
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


def _counter_rows(
    counter: Counter[str], limit: int | None = None
) -> list[dict[str, object]]:
    rows = counter.most_common(limit)
    return [{"label": label, "value": value} for label, value in rows]


def _quality_row(label: str, numerator: int, denominator: int) -> dict[str, object]:
    return {
        "label": label,
        "value": round(100 * numerator / denominator, 1) if denominator else 0,
        "detail": f"{numerator}/{denominator}",
    }


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
            self._error(404, "接口不存在")

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path not in {"/api/profile", "/api/advice", "/api/history"}:
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
                else:
                    self._json(
                        200,
                        service.history_page(
                            page=body.get("page", 1),
                            page_size=body.get("page_size", 10),
                            phone=body.get("phone"),
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

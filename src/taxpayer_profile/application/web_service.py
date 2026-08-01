"""Compatibility façade that composes focused UI application services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from taxpayer_profile.application.dashboard_service import DashboardService
from taxpayer_profile.application.history_service import HistoryService
from taxpayer_profile.application.profile_showcase_service import ProfileShowcaseService
from taxpayer_profile.application.web_dto import (
    district_unit_label as _district_unit_label,
    segmented_rows as _segmented_rows,
)
from taxpayer_profile.application.web_profile_support import (
    HISTORICAL_FACT_DEFINITIONS,
    PROFILE_DIMENSION_TAXONOMY,
    mode_payload,
)
from taxpayer_profile.auth import AuthService
from taxpayer_profile.config import Settings
from taxpayer_profile.database import create_schema, make_engine, make_session_factory
from taxpayer_profile.llm_client import OpenAICompatibleClient
from taxpayer_profile.query import query_profile_from_sessions
from taxpayer_profile.realtime_advice import (
    AdviceClient,
    build_fallback_advice,
    generate_realtime_advice,
)
from taxpayer_profile.security import PhoneProtector

REALTIME_ADVICE_TIMEOUT_SECONDS = 25.0


@dataclass
class ProfileAdviceService:
    """Phone-level profile lookup and real-time reception advice."""

    sessions: Callable[[], Session]
    protector: PhoneProtector
    settings: Settings
    advice_client_factory: Callable[[], AdviceClient | None] | None = None

    def lookup_profile(self, phone: object) -> dict[str, object] | None:
        return query_profile_from_sessions(
            phone=phone,
            sessions=self.sessions,
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
            empty_mode = mode_payload(
                {"proficiency_level": "暂无法判断", "emotion_state": "暂无法判断"}
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
    """Stable HTTP-facing façade; each method delegates to one focused use case."""

    database_path: Path
    protector: PhoneProtector
    settings: Settings
    advice_client_factory: Callable[[], AdviceClient | None] | None = None

    @cached_property
    def _sessions(self) -> sessionmaker[Session]:
        engine = make_engine(self.database_path)
        create_schema(engine)
        return make_session_factory(engine)

    @cached_property
    def auth(self) -> AuthService:
        return AuthService(self._sessions)

    @cached_property
    def profile_advice(self) -> ProfileAdviceService:
        return ProfileAdviceService(
            sessions=self._sessions,
            protector=self.protector,
            settings=self.settings,
            advice_client_factory=self.advice_client_factory,
        )

    @cached_property
    def dashboard(self) -> DashboardService:
        return DashboardService(self._sessions)

    @cached_property
    def showcase(self) -> ProfileShowcaseService:
        return ProfileShowcaseService(self._sessions, self.protector)

    @cached_property
    def history(self) -> HistoryService:
        return HistoryService(self._sessions, self.protector)

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
        return self.dashboard.summary()

    def profile_showcase_catalog(
        self, *, query: object = "", limit: object = 5
    ) -> dict[str, object]:
        return self.showcase.catalog(query=query, limit=limit)

    def profile_showcase(self, *, profile_key: object) -> dict[str, object]:
        return self.showcase.profile(profile_key=profile_key)

    def history_page(
        self, *, page: object = 1, page_size: object = 10, phone: object | None = None
    ) -> dict[str, object]:
        return self.history.page(page=page, page_size=page_size, phone=phone)

    def history_detail(self, business_id: object) -> dict[str, object] | None:
        return self.history.detail(business_id)

"""Shared interfaces and result types for model-backed analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from taxpayer_profile.ingestion.modes import InputMode
from taxpayer_profile.llm_client import (
    CallExtractionResult,
    RepeatIssueModelResult,
)


class AnalysisClient(Protocol):
    """Minimal model capabilities required by the ingestion application."""

    model: str

    def analyze_call(self, payload: dict[str, str | None]) -> CallExtractionResult: ...

    def analyze_repeat_issue(
        self, payload: dict[str, object]
    ) -> RepeatIssueModelResult: ...


@dataclass(frozen=True)
class EnrichmentMetadata:
    """Audit metadata produced alongside a normalized analytical result."""

    input_mode: InputMode
    analysis_source: str
    analysis_status: str
    model_name: str | None
    analysis_error: str | None = None


ModelExtraction = CallExtractionResult

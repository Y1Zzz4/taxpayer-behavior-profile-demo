"""Local candidate screening and explainable repeated-issue decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any


@dataclass(frozen=True)
class RepeatDecision:
    is_repeated_issue: bool | None
    matched_business_id: str | None = None
    matched_question: str | None = None
    previous_call_time: datetime | None = None
    previous_resolved: bool | None = None
    repeat_reason: str | None = None
    summary: str | None = None
    confidence: float | None = None
    candidate_score: float | None = None
    review_status: str = "not_required"
    needs_model_review: bool = False


def _value(item: object, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _normalize_question(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[\W_]+", "", value.lower())


def _similarity(first: str, second: str) -> float:
    if not first or not second:
        return 0.0
    return SequenceMatcher(None, first, second).ratio()


def analyze_repeat_issue(
    *,
    core_question: str | None,
    father_question: str | None,
    father_question_2: str | None,
    business_content: str | None,
    histories: list[object],
) -> RepeatDecision:
    """Screen all phone history locally before any optional model review."""

    if not histories:
        return RepeatDecision(
            False,
            summary="首次来电，无历史问题可匹配。",
            confidence=1.0,
            candidate_score=0.0,
        )

    current_questions = [
        _normalize_question(value)
        for value in (core_question, father_question, father_question_2, business_content)
        if value
    ]
    if not current_questions:
        return RepeatDecision(
            None,
            summary="当前问题信息不足，无法判断是否重复。",
            confidence=None,
            candidate_score=None,
            review_status="pending_review",
            needs_model_review=True,
        )

    best_item: object | None = None
    best_question: str | None = None
    best_score = 0.0
    for history in histories:
        for historical_question in (
            _value(history, "core_question"),
            _value(history, "father_question"),
            _value(history, "father_question_2"),
        ):
            normalized_history = _normalize_question(historical_question)
            for current in current_questions:
                score = _similarity(current, normalized_history)
                if score > best_score:
                    best_score = score
                    best_item = history
                    best_question = historical_question

    if best_item is None or best_score < 0.45:
        return RepeatDecision(
            False,
            summary="本地筛选未发现需复核的历史候选。",
            confidence=round(1.0 - best_score, 4),
            candidate_score=round(best_score, 4),
        )

    # Local text similarity is only candidate retrieval.  It must never turn a
    # substring match into a semantic decision (negation is a common counterexample).
    return RepeatDecision(
        None,
        matched_business_id=_value(best_item, "business_id"),
        matched_question=best_question,
        previous_call_time=_value(best_item, "call_time"),
        previous_resolved=_value(best_item, "resolved_status"),
        summary="本地筛选发现相近历史候选，等待语义复核。",
        confidence=None,
        candidate_score=round(best_score, 4),
        review_status="pending_review",
        needs_model_review=True,
    )

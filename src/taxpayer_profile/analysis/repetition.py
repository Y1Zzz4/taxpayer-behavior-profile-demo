"""Coordinate deterministic and model-assisted repeated-issue analysis.

The pure similarity rules remain in ``repeat_analysis``. This module owns the
application of an optional model decision and the chronology repair required
when an older call is imported after newer calls already exist.
"""

from __future__ import annotations

from taxpayer_profile.analysis.contracts import AnalysisClient
from taxpayer_profile.llm_client import RepeatIssueModelResult, build_repeat_payload
from taxpayer_profile.models import CallTrajectory
from taxpayer_profile.normalization import NormalizedCallInput
from taxpayer_profile.profiles.aggregation import trajectory_key
from taxpayer_profile.repeat_analysis import RepeatDecision, analyze_repeat_issue


def _analyze_values(
    *,
    core_question: str | None,
    father_question: str | None,
    father_question_2: str | None,
    business_content: str | None,
    histories: list[CallTrajectory],
    client: AnalysisClient | None,
    result_override: RepeatIssueModelResult | None = None,
) -> RepeatDecision:
    """Resolve one repeated-issue decision from rules and optional model review."""

    repeat = analyze_repeat_issue(
        core_question=core_question,
        father_question=father_question,
        father_question_2=father_question_2,
        business_content=business_content,
        histories=histories,
    )
    if not repeat.needs_model_review or client is None:
        return repeat

    result = result_override
    if result is None:
        result = client.analyze_repeat_issue(
            build_repeat_payload(
                core_question=core_question,
                father_question=father_question,
                father_question_2=father_question_2,
                business_content=business_content,
                histories=histories,
            )
        )
    if not result.is_repeated_issue:
        return RepeatDecision(
            False,
            repeat_reason=result.repeat_reason,
            summary=result.explanation,
            confidence=result.confidence,
            candidate_score=repeat.candidate_score,
            review_status="model_reviewed",
        )

    try:
        matched = histories[result.matched_history_index]  # type: ignore[index]
    except (IndexError, TypeError) as exc:
        # A validated JSON shape cannot prove that the index refers to the
        # current history payload, so this relationship is checked here.
        raise RuntimeError("模型返回了无效的历史匹配序号") from exc
    return RepeatDecision(
        True,
        matched_business_id=matched.business_id,
        matched_question=(
            matched.core_question or matched.father_question or matched.father_question_2
        ),
        previous_call_time=matched.call_time,
        previous_resolved=matched.resolved_status,
        repeat_reason=result.repeat_reason,
        summary=result.explanation,
        confidence=result.confidence,
        candidate_score=repeat.candidate_score,
        review_status="model_reviewed",
    )


def analyze_repeat(
    call: NormalizedCallInput,
    histories: list[CallTrajectory],
    client: AnalysisClient | None,
    result_override: RepeatIssueModelResult | None = None,
) -> RepeatDecision:
    """Analyze whether a normalized call repeats an earlier issue."""

    return _analyze_values(
        core_question=call.core_question,
        father_question=call.father_question,
        father_question_2=call.father_question_2,
        business_content=call.business_content,
        histories=histories,
        client=client,
        result_override=result_override,
    )


def _apply_decision(
    trajectory: CallTrajectory, decision: RepeatDecision
) -> None:
    trajectory.is_repeated_issue = decision.is_repeated_issue
    trajectory.matched_previous_business_id = decision.matched_business_id
    trajectory.matched_previous_question = decision.matched_question
    trajectory.matched_previous_call_time = decision.previous_call_time
    trajectory.previous_issue_resolved = decision.previous_resolved
    trajectory.repeat_reason = decision.repeat_reason
    trajectory.repeat_summary = decision.summary
    trajectory.repeat_candidate_score = decision.candidate_score
    trajectory.repeat_confidence = decision.confidence
    trajectory.repeat_review_status = decision.review_status
    if decision.needs_model_review:
        trajectory.analysis_status = "pending_review"


def _resequence_history(
    trajectories: list[CallTrajectory],
) -> list[CallTrajectory]:
    """Recompute chronology-derived fields for an entire phone history."""

    ordered = sorted(trajectories, key=trajectory_key)
    for index, trajectory in enumerate(ordered):
        previous = ordered[index - 1] if index else None
        trajectory.is_repeated_call = previous is not None
        trajectory.previous_call_time = previous.call_time if previous else None
        trajectory.call_interval = (
            int((trajectory.call_time - previous.call_time).total_seconds())
            if previous
            else None
        )
        trajectory.historical_call_count = index + 1
    return ordered


def reassess_after_backfill(
    trajectories: list[CallTrajectory],
    *,
    new_business_ids: set[str],
    client: AnalysisClient | None,
) -> list[CallTrajectory]:
    """Repair derived chronology after calls older than existing data arrive.

    Explicit manual review is authoritative and is never overwritten by an
    automatic backfill reassessment.
    """

    ordered = _resequence_history(trajectories)
    new_items = [item for item in ordered if item.business_id in new_business_ids]
    if not new_items:
        return ordered
    earliest_new_key = min(trajectory_key(item) for item in new_items)
    for index, trajectory in enumerate(ordered):
        if trajectory.business_id in new_business_ids:
            continue
        if trajectory.repeat_review_status == "manually_reviewed":
            continue
        if trajectory_key(trajectory) <= earliest_new_key:
            continue
        decision = _analyze_values(
            core_question=trajectory.core_question,
            father_question=trajectory.father_question,
            father_question_2=trajectory.father_question_2,
            business_content=None,
            histories=ordered[:index],
            client=client,
        )
        _apply_decision(trajectory, decision)
    return ordered

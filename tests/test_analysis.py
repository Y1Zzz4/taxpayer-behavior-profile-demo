from datetime import datetime

from taxpayer_profile.profiling import (
    analyze_service,
    attention_and_service_strategy,
    build_service_strategy,
    classify_service_profile,
    weighted_proficiency,
)
from taxpayer_profile.models import CallTrajectory
from taxpayer_profile.processor import _reassess_after_backfill
from taxpayer_profile.repeat_analysis import analyze_repeat_issue


def test_weighted_proficiency_favors_recent_calls() -> None:
    score = weighted_proficiency(
        [
            (datetime(2026, 6, 1), 2.0),
            (datetime(2026, 6, 9), 8.0),
        ]
    )
    assert score == 6.0


def test_similar_history_is_only_a_model_review_candidate() -> None:
    decision = analyze_repeat_issue(
        core_question="电子税务局如何提交增值税申报",
        father_question="增值税申报操作",
        father_question_2=None,
        business_content="想确认提交步骤",
        histories=[
            {
                "business_id": "OLD-1",
                "call_time": datetime(2026, 6, 1),
                "core_question": "增值税申报如何在电子税务局提交",
                "father_question": "增值税申报操作",
                "father_question_2": None,
                "resolved_status": False,
            }
        ],
    )

    assert decision.is_repeated_issue is None
    assert decision.matched_business_id == "OLD-1"
    assert decision.needs_model_review is True
    assert decision.review_status == "pending_review"
    assert decision.confidence is None
    assert decision.candidate_score == 1.0


def test_negated_topic_is_never_confirmed_by_substring_matching() -> None:
    decision = analyze_repeat_issue(
        core_question="不是咨询增值税申报，我要办理企业注销",
        father_question=None,
        father_question_2=None,
        business_content=None,
        histories=[
            {
                "business_id": "OLD-1",
                "call_time": datetime(2026, 6, 1),
                "core_question": "增值税申报",
                "resolved_status": True,
            }
        ],
    )

    assert decision.is_repeated_issue is not True
    assert decision.needs_model_review is True


def test_first_call_is_not_a_repeated_issue() -> None:
    decision = analyze_repeat_issue(
        core_question="如何申报",
        father_question=None,
        father_question_2=None,
        business_content=None,
        histories=[],
    )
    assert decision.is_repeated_issue is False
    assert decision.confidence == 1.0


def test_backfill_does_not_override_an_optional_manual_review() -> None:
    common = {
        "phone_hash": "a" * 64,
        "core_question": "增值税申报",
        "historical_call_count": 1,
        "analysis_status": "completed",
        "analysis_version": "test",
    }
    old = CallTrajectory(
        business_id="OLD", call_time=datetime(2026, 6, 1), **common
    )
    backfill = CallTrajectory(
        business_id="BACKFILL", call_time=datetime(2026, 6, 2), **common
    )
    reviewed = CallTrajectory(
        business_id="REVIEWED",
        call_time=datetime(2026, 6, 3),
        is_repeated_issue=True,
        repeat_review_status="manually_reviewed",
        repeat_confidence=1.0,
        **common,
    )

    _reassess_after_backfill(
        [old, backfill, reviewed],
        new_business_ids={"BACKFILL"},
        client=None,
    )

    assert reviewed.is_repeated_issue is True
    assert reviewed.repeat_review_status == "manually_reviewed"
    assert reviewed.repeat_confidence == 1.0


def test_service_analysis_uses_neutral_traceable_labels() -> None:
    result = analyze_service(
        resolved=False,
        waiting=True,
        pushback=False,
        dissatisfied=False,
        has_answer=True,
    )
    assert result.rating == "一般"
    assert "未直接解决" in result.summary


def test_attention_strategy_avoids_derogatory_labels() -> None:
    level, mode, suggestion = attention_and_service_strategy(
        total_calls=4,
        repeated_issues=2,
        unresolved=3,
        abnormal_ends=0,
        dissatisfaction=0,
        has_pushback=False,
        latest_resolved=False,
        proficiency_score=4.0,
    )
    assert level == "重点关注"
    assert mode == "事项进度核验与闭环型"
    assert "未解决事项" in suggestion


def test_service_strategy_is_personalized_and_modes_are_distinct() -> None:
    professional = build_service_strategy(
        total_calls=1,
        repeated_issues=0,
        unresolved=0,
        abnormal_ends=0,
        dissatisfaction=0,
        has_pushback=False,
        latest_resolved=True,
        proficiency_score=8.5,
        latest_question="境外奖金计税方式",
    )
    guided = build_service_strategy(
        total_calls=1,
        repeated_issues=0,
        unresolved=0,
        abnormal_ends=0,
        dissatisfaction=0,
        has_pushback=False,
        latest_resolved=True,
        proficiency_score=3.0,
        latest_question="电子税务局申报",
    )

    assert professional.recommended_mode == "结论条件优先型"
    assert "境外奖金计税方式" in professional.suggestion
    assert "不展开通用入门步骤" in professional.suggestion
    assert guided.recommended_mode == "分步操作陪伴确认型"
    assert "一次给出1—2个操作步骤" in guided.suggestion
    assert professional.suggestion != guided.suggestion


def test_latest_unresolved_and_work_order_get_follow_up_modes() -> None:
    unresolved = build_service_strategy(
        total_calls=1,
        repeated_issues=0,
        unresolved=1,
        abnormal_ends=0,
        dissatisfaction=0,
        has_pushback=False,
        latest_resolved=False,
        proficiency_score=8.0,
        latest_question="社保欠费缴纳",
        unresolved_questions=["社保欠费缴纳"],
    )
    work_order = build_service_strategy(
        total_calls=1,
        repeated_issues=0,
        unresolved=0,
        abnormal_ends=0,
        dissatisfaction=0,
        has_pushback=False,
        latest_resolved=None,
        proficiency_score=8.0,
        work_orders=0,
        latest_question="查询工单进度",
    )

    assert unresolved.recommended_mode == "事项进度核验与闭环型"
    assert "社保欠费缴纳" in unresolved.suggestion
    assert work_order.recommended_mode == "事项进度核验与闭环型"
    assert "受理时间、当前状态和承办节点" in work_order.suggestion


def test_primary_service_profile_uses_four_clear_priority_rules() -> None:
    common = {
        "total_calls": 4,
        "repeated_issues": 1,
        "unresolved": 1,
        "work_orders": 1,
        "proficiency_score": 9.0,
    }
    assert (
        classify_service_profile(**common, dissatisfaction=1).profile_type
        == "服务关注型"
    )
    assert (
        classify_service_profile(**common, dissatisfaction=0).profile_type
        == "事项跟进型"
    )
    assert (
        classify_service_profile(
            total_calls=3,
            repeated_issues=0,
            unresolved=0,
            work_orders=0,
            dissatisfaction=0,
            proficiency_score=9.0,
        ).profile_type
        == "持续咨询型"
    )
    assert (
        classify_service_profile(
            total_calls=1,
            repeated_issues=0,
            unresolved=0,
            work_orders=0,
            dissatisfaction=0,
            proficiency_score=3.0,
        ).profile_type
        == "常规服务型"
    )

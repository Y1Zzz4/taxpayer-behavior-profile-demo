from taxpayer_profile.ingestion.policy import (
    INCREMENTAL_REUSE_POLICY,
    TRUSTED_HISTORY_REUSE_POLICY,
)
from taxpayer_profile.normalization import normalize_call_row


def test_incremental_policy_reuses_only_confirmed_analytical_fields() -> None:
    expected = {
        "大模型核心问题",
        "一级专题类别",
        "二级标签",
        "申请人员身份",
    }

    assert INCREMENTAL_REUSE_POLICY.reusable_analysis_columns == expected
    assert TRUSTED_HISTORY_REUSE_POLICY.reusable_analysis_columns > expected


def test_incremental_normalization_ignores_optional_legacy_decisions() -> None:
    call = normalize_call_row(
        {
            "业务编号": "BIZ-1",
            "来电号码": "13800000001",
            "登记日期": "2026/7/24 09:00",
            "业务内容": "本通正常结束",
            "登记处理方式": "1001",
            "大模型核心问题": "如何办理增值税申报",
            "一级专题类别": "增值税",
            "二级标签": "增值税-申报",
            "申请人员身份": "cwfzr",
            # These legacy results may still be present, but a future
            # incremental file is not required to provide or preserve them.
            "是否工单": True,
            "非正常中断": True,
            "需求类别": "政策咨询类",
            "father_question": "旧父问题",
            "咨询主体(大模型判断)": "个人",
        },
        reuse_policy=INCREMENTAL_REUSE_POLICY,
    )

    assert call.core_question == "如何办理增值税申报"
    assert call.topic_category == "增值税"
    assert call.secondary_topic == "增值税-申报"
    assert call.raw_identity_label == "cwfzr"
    assert call.work_order is False
    assert call.rule_abnormal_end is False
    assert call.demand_category is None
    assert call.father_question is None
    assert call.caller_type is None


def test_incremental_normalization_accepts_missing_optional_legacy_columns() -> None:
    call = normalize_call_row(
        {
            "业务编号": "BIZ-2",
            "来电号码": "13800000002",
            "登记日期": "2026/7/24 10:00",
            "登记处理方式": "1404",
        },
        reuse_policy=INCREMENTAL_REUSE_POLICY,
    )

    # Work-order status remains derivable from a source processing code even
    # when the old analytical flag is absent.
    assert call.work_order is True
    assert call.rule_abnormal_end is False

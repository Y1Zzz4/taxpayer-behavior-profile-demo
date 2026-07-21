from datetime import datetime

import pandas as pd
import pytest

from taxpayer_profile.identity import (
    infer_enterprise_identity,
    map_identity_label,
    resolve_enterprise_identity,
)
from taxpayer_profile.normalization import (
    determine_rule_abnormal_end,
    determine_work_order,
    normalize_call_row,
    normalize_boolean,
    parse_datetime,
)


def test_parse_registration_date() -> None:
    assert parse_datetime("2026/6/5 13:44") == datetime(2026, 6, 5, 13, 44)
    assert parse_datetime(pd.Timestamp("2026-06-10 08:30")) == datetime(
        2026, 6, 10, 8, 30
    )
    assert parse_datetime("not-a-date") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (0, False),
        ("是", True),
        ("false", False),
        ("", None),
        (pd.NA, None),
        ("异常", None),
    ],
)
def test_normalize_boolean(raw: object, expected: bool | None) -> None:
    assert normalize_boolean(raw) is expected


def test_identity_label_mapping() -> None:
    assert map_identity_label("fddbr") == "法定代表人"
    assert map_identity_label("cwfzr") == "财务负责人"
    assert map_identity_label("bsry") == "办税人员"
    assert map_identity_label("qt") == "其他"
    assert map_identity_label("zrr") == "自然人"


def test_enterprise_identity_prefers_explicit_text_then_source_label() -> None:
    assert infer_enterprise_identity("个人", "fddbr", "我是法人") == "不适用"
    assert infer_enterprise_identity("企业", "cwfzr", "咨询申报问题") == "财务负责人"
    assert (
        infer_enterprise_identity("企业", "cwfzr", "我是公司的财务负责人")
        == "财务负责人"
    )
    assert infer_enterprise_identity("企业", "cwfzr", "我是办税员") == "办税人员"
    assert infer_enterprise_identity("企业", "cwfzr", "我是公司会计") == "财务负责人"
    assert infer_enterprise_identity("企业", "qt", "我是公司会计") == "其他"
    assert infer_enterprise_identity("企业", "zrr", "咨询申报问题") == "无法判断"
    assert infer_enterprise_identity("企业", None, "我是公司会计") == "其他"


def test_model_identity_resolution_tracks_source_and_conflict() -> None:
    fallback = resolve_enterprise_identity(
        caller_type="企业", raw_identity="cwfzr", explicit_identity="无法判断"
    )
    assert (fallback.identity, fallback.source, fallback.conflict) == (
        "财务负责人",
        "source_label",
        False,
    )
    override = resolve_enterprise_identity(
        caller_type="企业", raw_identity="cwfzr", explicit_identity="办税人员"
    )
    assert (override.identity, override.source, override.conflict) == (
        "办税人员",
        "transcript",
        True,
    )


def test_work_order_uses_existing_field_or_processing_code() -> None:
    assert determine_work_order(False, "1404") is True
    assert determine_work_order(None, "1404.0") is True
    assert determine_work_order("是", "1001") is True
    assert determine_work_order("否", "1001") is False


def test_trusted_resolved_field_is_used_as_a_fallback_and_time_source_is_kept() -> None:
    call = normalize_call_row(
        {
            "业务编号": "BIZ-1",
            "来电号码": "13800000001",
            "登记日期": "2026/6/5 13:44",
            "通话结束时间": "2026/6/5 13:50",
            "坐席是否解决纳税人问题": "是",
        }
    )

    assert call.resolved_status is True
    assert call.call_time == datetime(2026, 6, 5, 13, 44)
    assert call.call_time_source == "registration_fallback"
    assert call.call_end_time == datetime(2026, 6, 5, 13, 50)


def test_rule_abnormal_end() -> None:
    assert determine_rule_abnormal_end("", "通话中断", "") is True
    assert determine_rule_abnormal_end("纳税人：我再问一下", "", "") is True
    assert determine_rule_abnormal_end("坐席：谢谢来电，再见", "", "") is False
    assert determine_rule_abnormal_end("纳税人：好的，明白了", "", "") is False
    assert determine_rule_abnormal_end("", "本次未中断", "正常结束") is False
    assert determine_rule_abnormal_end("系统记录：本次未中断", "", "") is False
    assert (
        determine_rule_abnormal_end(
            "纳税人：谢谢\n坐席：还需要确认一个信息\n纳税人：我这边",
            "",
            "",
        )
        is True
    )
    assert determine_rule_abnormal_end("", "正常咨询", "已答复") is False

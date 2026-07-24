import pytest

from taxpayer_profile.analysis.enrichment import enrich_call
from taxpayer_profile.ingestion.modes import InputMode
from taxpayer_profile.ingestion.policy import INCREMENTAL_REUSE_POLICY
from taxpayer_profile.normalization import normalize_call_row


def test_incremental_call_without_text_is_rejected_before_unknown_type_is_saved() -> None:
    """Caller type is binary; an evidence-free row cannot enter the database."""

    call = normalize_call_row(
        {
            "业务编号": "EMPTY-1",
            "来电号码": "13800000001",
            "登记日期": "2026/7/24 09:00",
            "登记处理方式": "1001",
        },
        reuse_policy=INCREMENTAL_REUSE_POLICY,
    )

    with pytest.raises(ValueError, match="缺少用于判定咨询主体的文本信息"):
        enrich_call(call, InputMode.INCREMENTAL, client=None)

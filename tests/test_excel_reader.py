from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from taxpayer_profile.excel_reader import read_excel_records


def test_reader_uses_registration_date_and_allowed_fields(tmp_path: Path) -> None:
    workbook = tmp_path / "calls.xlsx"
    pd.DataFrame(
        {
            "业务编号": ["B1", "B2", "B3"],
            "来电号码": ["13800000001", "13800000002", "13800000003"],
            "登记日期": ["2026/6/9 09:00", "2026/6/10 09:00", "2026/6/11 09:00"],
            "通话开始时间": ["2026/6/9 08:59", "2026/6/10 08:59", "2026/6/11 08:59"],
            "登记处理方式": ["1404", "1001", "1001"],
            "转写结果": ["谢谢", "谢谢", "谢谢"],
            "二级标签": ["申报-增值税", "申报-个人所得税", "发票-数电票"],
            "不允许读取的列": ["private-a", "private-b", "private-c"],
        }
    ).to_excel(workbook, index=False)

    frame = read_excel_records(workbook, date(2026, 6, 10), date(2026, 6, 10))

    assert len(frame) == 1
    assert frame.iloc[0]["业务编号"] == "B2"
    assert "不允许读取的列" not in frame.columns
    assert str(frame.iloc[0]["登记处理方式"]) == "1001"
    assert frame.iloc[0]["二级标签"] == "申报-个人所得税"


def test_reader_validates_required_columns(tmp_path: Path) -> None:
    workbook = tmp_path / "calls.xlsx"
    pd.DataFrame({"业务编号": ["B1"]}).to_excel(workbook, index=False)

    with pytest.raises(ValueError, match="缺少必要字段"):
        read_excel_records(workbook, date(2026, 6, 1), date(2026, 6, 9))


def test_reader_requires_an_original_business_evidence_column(tmp_path: Path) -> None:
    workbook = tmp_path / "calls.xlsx"
    pd.DataFrame(
        {
            "业务编号": ["B1"],
            "来电号码": ["13800000001"],
            "登记日期": ["2026/6/10 09:00"],
            "大模型核心问题": ["只有已有分析结果不能替代原始业务信息"],
        }
    ).to_excel(workbook, index=False)

    with pytest.raises(ValueError, match="原始业务信息字段"):
        read_excel_records(workbook, date(2026, 6, 1), date(2026, 6, 30))


def test_reader_requires_source_evidence_on_every_row(tmp_path: Path) -> None:
    workbook = tmp_path / "calls.xlsx"
    pd.DataFrame(
        {
            "业务编号": ["B1", "B2"],
            "来电号码": ["13800000001", "13800000002"],
            "登记日期": ["2026/6/10 09:00", "2026/6/10 10:00"],
            "转写结果": ["有效转写", None],
            "业务内容": [None, "  "],
            "答复内容": [None, None],
        }
    ).to_excel(workbook, index=False)

    with pytest.raises(ValueError, match="第 2 条记录缺少原始业务信息"):
        read_excel_records(workbook, date(2026, 6, 1), date(2026, 6, 30))

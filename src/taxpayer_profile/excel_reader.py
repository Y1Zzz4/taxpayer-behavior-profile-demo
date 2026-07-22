"""Whitelisted, date-based reading of the single-sheet source workbook."""

from __future__ import annotations

import hashlib
from datetime import date
from enum import Enum
from pathlib import Path

import pandas as pd


class InputMode(str, Enum):
    TRUSTED_IMPORT = "trusted_import"
    RAW_ANALYSIS = "raw_analysis"
    BOOTSTRAP_MIXED = "bootstrap_mixed"

ALLOWED_COLUMNS = {
    "业务编号",
    "转写结果",
    "登记日期",
    "通话开始时间",
    "通话结束时间",
    "坐席工号",
    "坐席姓名",
    "业务内容",
    "答复内容",
    "录音路径",
    "登记单位",
    "登记处理方式",
    "业务类别",
    "来电号码",
    "满意度",
    "呼叫流水号",
    "一级专题类别",
    "二级标签",
    "申请人员身份",
    "大模型核心问题",
    "需求类别",
    "father_question",
    "father_question_2",
    "咨询主体(大模型判断)",
    "是否工单",
    "非正常中断",
    "是否非正常中断（大模型判断）",
    "是否存在让纳税人等待表述",
    "坐席是否存在潜在推诿行为",
    "纳税人是否对坐席存在不满",
    "坐席是否解决纳税人问题",
    "是否联系相关人员或部门",
    "联系对象",
    "是否主动联系相关人员或部门",
    "是否未直接解决问题",
    "自然问答轮次",
    "大模型核心问题轮次",
    "有效问答轮次",
    "有效问答内容",
}

REQUIRED_COLUMNS = {"业务编号", "来电号码", "登记日期"}
STRING_COLUMNS = {
    "业务编号": "string",
    "来电号码": "string",
    "坐席工号": "string",
    "呼叫流水号": "string",
    "登记处理方式": "string",
    "申请人员身份": "string",
}


def read_excel_records(
    input_path: Path | str, start_date: date, end_date: date
) -> pd.DataFrame:
    """Read approved fields and filter inclusively by registration date."""

    return read_excel_workbook(input_path, start_date=start_date, end_date=end_date)


def read_excel_workbook(
    input_path: Path | str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    """Read approved fields, optionally filtering by registration date."""

    source = Path(input_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"输入文件不存在：{source}")
    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError("结束日期不能早于开始日期")

    header = pd.read_excel(source, sheet_name=0, nrows=0)
    missing = REQUIRED_COLUMNS.difference(header.columns)
    if missing:
        raise ValueError(f"缺少必要字段：{', '.join(sorted(missing))}")

    frame = pd.read_excel(
        source,
        sheet_name=0,
        usecols=lambda column: column in ALLOWED_COLUMNS,
        dtype=STRING_COLUMNS,
    )
    registration = pd.to_datetime(frame["登记日期"], errors="coerce")
    selected = frame.copy()
    dates = registration.dt.date
    if start_date is not None:
        selected = selected.loc[dates >= start_date].copy()
    if end_date is not None:
        selected = selected.loc[dates <= end_date].copy()
    selected["登记日期"] = registration.loc[selected.index]
    return selected.reset_index(drop=True)


def workbook_registration_bounds(input_path: Path | str) -> tuple[date, date]:
    """Read only the registration-date column to order independent batches."""

    source = Path(input_path).expanduser().resolve()
    frame = pd.read_excel(source, sheet_name=0, usecols=["登记日期"])
    parsed = pd.to_datetime(frame["登记日期"], errors="coerce").dropna()
    if parsed.empty:
        raise ValueError(f"文件没有有效登记日期：{source.name}")
    return parsed.min().date(), parsed.max().date()


def workbook_fingerprint(path: Path | str) -> str:
    source = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_workbooks(raw_directory: Path | str) -> list[Path]:
    directory = Path(raw_directory).expanduser().resolve()
    if not directory.is_dir():
        raise NotADirectoryError(f"原始数据目录不存在：{directory}")
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and not path.name.startswith("~$")
            and path.suffix.lower() in {".xlsx", ".xlsm"}
        ),
        key=lambda item: item.name.lower(),
    )

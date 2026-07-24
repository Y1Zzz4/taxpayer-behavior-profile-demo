"""Whitelisted adapter for the current single-sheet Excel input contract."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from taxpayer_profile.ingestion.contracts import InputSourceIdentity
from taxpayer_profile.ingestion.policy import (
    SOURCE_FACT_COLUMNS,
    WORKBOOK_ANALYSIS_COLUMNS,
)
from taxpayer_profile.ingestion.schema import (
    REQUIRED_COLUMNS,
    validate_input_columns,
    validate_input_rows,
)

ALLOWED_COLUMNS = SOURCE_FACT_COLUMNS | WORKBOOK_ANALYSIS_COLUMNS
STRING_COLUMNS = {
    "业务编号": "string",
    "来电号码": "string",
    "坐席工号": "string",
    "呼叫流水号": "string",
    "登记处理方式": "string",
    "申请人员身份": "string",
}


@dataclass(frozen=True)
class ExcelInputAdapter:
    """Adapt the current whitelisted workbook format to generic input rows."""

    def identify(self, source: Path) -> InputSourceIdentity:
        return InputSourceIdentity(
            name=source.name,
            fingerprint=workbook_fingerprint(source),
        )

    def read_rows(
        self,
        source: Path,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict[str, object]]:
        frame = read_excel_workbook(
            source,
            start_date=start_date,
            end_date=end_date,
        )
        return frame.to_dict(orient="records")


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
    validate_input_columns(header.columns)

    frame = pd.read_excel(
        source,
        sheet_name=0,
        usecols=lambda column: column in ALLOWED_COLUMNS,
        dtype=STRING_COLUMNS,
    )
    validate_input_rows(frame.to_dict(orient="records"))
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
    """Hash the complete source file for batch-level idempotency."""

    source = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_workbooks(raw_directory: Path | str) -> list[Path]:
    """Discover supported workbooks while ignoring Office temporary files."""

    directory = Path(raw_directory).expanduser().resolve()
    if not directory.is_dir():
        raise NotADirectoryError(f"增量数据目录不存在：{directory}")
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

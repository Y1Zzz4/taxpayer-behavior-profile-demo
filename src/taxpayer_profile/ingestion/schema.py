"""Format-independent schema validation for incremental input rows."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

import pandas as pd

REQUIRED_COLUMNS = frozenset({"业务编号", "来电号码", "登记日期"})
SOURCE_EVIDENCE_COLUMNS = frozenset({"转写结果", "业务内容", "答复内容"})


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    try:
        missing = pd.isna(value)
        if isinstance(missing, bool):
            return not missing
        return not bool(missing)
    except (TypeError, ValueError):
        return True


def validate_input_columns(columns: Collection[str]) -> None:
    """Validate structural keys and the presence of an original evidence field."""

    available = set(columns)
    missing = REQUIRED_COLUMNS.difference(available)
    if missing:
        raise ValueError(f"缺少必要字段：{', '.join(sorted(missing))}")
    if SOURCE_EVIDENCE_COLUMNS.isdisjoint(available):
        choices = "、".join(sorted(SOURCE_EVIDENCE_COLUMNS))
        raise ValueError(f"至少需要包含一个原始业务信息字段：{choices}")


def validate_input_rows(rows: Sequence[Mapping[str, object]]) -> None:
    """Reject rows that cannot be identified or analyzed from source evidence."""

    if not rows:
        return
    all_columns = {column for row in rows for column in row}
    validate_input_columns(all_columns)

    invalid_structure: list[int] = []
    missing_evidence: list[int] = []
    for position, row in enumerate(rows, start=1):
        if any(not _has_value(row.get(column)) for column in REQUIRED_COLUMNS):
            invalid_structure.append(position)
        if not any(_has_value(row.get(column)) for column in SOURCE_EVIDENCE_COLUMNS):
            missing_evidence.append(position)

    if invalid_structure:
        positions = "、".join(map(str, invalid_structure[:10]))
        raise ValueError(f"第 {positions} 条记录的必要字段为空")
    if missing_evidence:
        positions = "、".join(map(str, missing_evidence[:10]))
        choices = "、".join(sorted(SOURCE_EVIDENCE_COLUMNS))
        raise ValueError(
            f"第 {positions} 条记录缺少原始业务信息；"
            f"{choices}至少一项必须非空"
        )

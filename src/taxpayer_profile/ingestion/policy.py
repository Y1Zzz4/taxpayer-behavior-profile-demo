"""Explicit contracts for reusing previously derived workbook fields.

Source facts such as call time, transcript and business content are always
imported by the normalizer. This module governs only fields that may contain a
previous analytical result. Keeping that decision in one place prevents a new
input column from being trusted accidentally merely because it is present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


# These are the source facts accepted by the current workbook adapter. They are
# documented here so callers can distinguish input evidence from derived
# analytical values; the reader remains responsible for its concrete schema.
SOURCE_FACT_COLUMNS = frozenset(
    {
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
    }
)

# Analytical columns recognized by the current Excel adapter. Recognition does
# not imply trust: each ingestion policy below selects its own subset.
WORKBOOK_ANALYSIS_COLUMNS = frozenset(
    {
        "大模型核心问题",
        "一级专题类别",
        "二级标签",
        "申请人员身份",
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
)

@dataclass(frozen=True)
class FieldReusePolicy:
    """Whitelist analytical fields that an ingestion path may trust.

    A missing optional field always resolves to ``None``. This is intentional:
    future incremental files are not required to carry legacy flags such as
    ``是否工单`` or ``非正常中断``.
    """

    name: str
    reusable_analysis_columns: frozenset[str]

    def value(self, row: Mapping[str, object], column: str) -> object | None:
        """Return an input value only when this policy explicitly trusts it."""

        if column not in self.reusable_analysis_columns:
            return None
        return row.get(column)

    def reuses(self, column: str) -> bool:
        """Expose the whitelist decision for validation and diagnostics."""

        return column in self.reusable_analysis_columns


INCREMENTAL_REUSE_POLICY = FieldReusePolicy(
    name="incremental",
    reusable_analysis_columns=frozenset(
        {
            "大模型核心问题",
            "一级专题类别",
            "二级标签",
            # The label is supporting evidence for deterministic identity
            # resolution; it is not copied blindly to the final identity.
            "申请人员身份",
        }
    ),
)

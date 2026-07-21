"""Deterministic parsing and normalization for approved Excel fields."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from taxpayer_profile.identity import infer_enterprise_identity, normalize_caller_type
from taxpayer_profile.security import normalize_phone

TRUE_VALUES = {"true", "1", "是", "有", "存在", "已解决", "y", "yes"}
FALSE_VALUES = {"false", "0", "否", "无", "不存在", "未解决", "n", "no"}
ABNORMAL_END_PATTERNS = (
    r"(?:电话|通话|线路|对话|语音)?(?:突然|意外|异常)?中断",
    r"(?:对方|来电人|纳税人|坐席)?(?:突然|直接|已经|已)?挂断",
)
NEGATED_END_PATTERNS = (
    r"(?:未|没有|没|并未|并没有|无|不是|不属于)(?:发生|出现)?(?:电话|通话|线路|对话|语音)?(?:中断|挂断)",
    r"(?:中断|挂断)(?:情况)?(?:未发生|未出现|不存在)",
)


def is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if result is pd.NA:
        return True
    try:
        return bool(result)
    except (TypeError, ValueError):
        return False


def text_or_none(value: object) -> str | None:
    if is_missing(value):
        return None
    rendered = str(value).strip()
    return rendered or None


def clean_identifier(value: object) -> str | None:
    rendered = text_or_none(value)
    if rendered is None:
        return None
    return rendered[:-2] if rendered.endswith(".0") else rendered


def parse_datetime(value: object) -> datetime | None:
    if is_missing(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    if isinstance(parsed, pd.Timestamp):
        return parsed.to_pydatetime().replace(tzinfo=None)
    return None


def normalize_boolean(value: object) -> bool | None:
    if is_missing(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value in {0.0, 1.0}:
            return bool(value)
    rendered = str(value).strip().lower()
    if rendered in TRUE_VALUES:
        return True
    if rendered in FALSE_VALUES:
        return False
    return None


def determine_work_order(existing_value: object, processing_method: object) -> bool:
    existing = normalize_boolean(existing_value)
    code = clean_identifier(processing_method)
    return existing is True or code == "1404"


def determine_rule_abnormal_end(
    transcript: object, business_content: object, answer_content: object
) -> bool:
    """Identify only explicit source evidence of an interruption or hang-up.

    Missing courtesy or closure words are not evidence of an abnormal ending.
    Semantic cases such as an unresolved transfer are handled separately by the
    model field, while this deterministic field remains conservative.
    """

    business_and_answer = "".join(
        filter(None, (text_or_none(business_content), text_or_none(answer_content)))
    )
    evidence_text = business_and_answer
    for pattern in NEGATED_END_PATTERNS:
        evidence_text = re.sub(pattern, "", evidence_text)
    if any(re.search(pattern, evidence_text) for pattern in ABNORMAL_END_PATTERNS):
        return True

    transcript_text = text_or_none(transcript)
    if transcript_text is None:
        return False

    utterances = [
        part.strip()
        for part in re.split(
            r"(?:\r?\n)+|(?=(?:坐席|客服|纳税人|来电人|客户)[：:])",
            transcript_text,
        )
        if part.strip()
    ]
    final_utterance = utterances[-1] if utterances else transcript_text
    terminal_text = final_utterance[-120:].strip()
    has_negated_marker = any(
        re.search(pattern, terminal_text) for pattern in NEGATED_END_PATTERNS
    )
    cleaned_terminal = terminal_text
    for pattern in NEGATED_END_PATTERNS:
        cleaned_terminal = re.sub(pattern, "", cleaned_terminal)
    if any(re.search(pattern, cleaned_terminal) for pattern in ABNORMAL_END_PATTERNS):
        return True
    if has_negated_marker:
        return False

    return False


def int_or_none(value: object) -> int | None:
    if is_missing(value):
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class NormalizedCallInput:
    business_id: str | None
    phone: str | None
    registration_time: datetime | None
    call_time: datetime | None
    call_time_source: str | None
    raw_call_start_time: datetime | None
    call_end_time: datetime | None
    transcript: str | None
    agent_id: str | None
    agent_name: str | None
    business_content: str | None
    answer_content: str | None
    recording_path: str | None
    registration_unit: str | None
    handling_method: str | None
    business_category: str | None
    satisfaction: str | None
    call_serial_number: str | None
    core_question: str | None
    topic_category: str | None
    demand_category: str | None
    father_question: str | None
    father_question_2: str | None
    caller_type: str | None
    enterprise_identity: str
    resolved_status: bool | None
    unresolved_reason: str | None
    work_order: bool
    rule_abnormal_end: bool
    model_abnormal_end: bool | None
    waiting_expression: bool | None
    potential_pushback: bool | None
    taxpayer_dissatisfied: bool | None
    contacted_other_department: bool | None
    active_contacted_other_department: bool | None
    contact_target: str | None
    natural_qa_turns: int | None
    core_question_turns: int | None
    effective_qa_turns: int | None
    effective_qa_content: str | None
    raw_identity_label: str | None
    proficiency_score: float | None
    proficiency_summary: str
    proficiency_level: str
    proficiency_basis: str
    emotion_state: str
    emotion_basis: str
    service_rating: str | None
    service_summary: str | None
    enterprise_identity_source: str
    enterprise_identity_conflict: bool


def normalize_call_row(
    row: dict[str, Any], *, trust_analyzed_fields: bool = True
) -> NormalizedCallInput:
    registration_time = parse_datetime(row.get("登记日期"))
    call_start = parse_datetime(row.get("通话开始时间"))
    transcript = text_or_none(row.get("转写结果"))
    caller_type = (
        normalize_caller_type(row.get("咨询主体(大模型判断)"))
        if trust_analyzed_fields
        else None
    )
    unresolved = (
        normalize_boolean(row.get("是否未直接解决问题"))
        if trust_analyzed_fields
        else None
    )
    resolved = (
        normalize_boolean(row.get("坐席是否解决纳税人问题"))
        if trust_analyzed_fields
        else None
    )
    enterprise_identity = (
        infer_enterprise_identity(caller_type, row.get("申请人员身份"), transcript)
        if trust_analyzed_fields
        else "无法判断"
    )
    return NormalizedCallInput(
        business_id=clean_identifier(row.get("业务编号")),
        phone=normalize_phone(row.get("来电号码")),
        registration_time=registration_time,
        call_time=call_start or registration_time,
        call_time_source=(
            "call_start" if call_start is not None else "registration_fallback"
            if registration_time is not None
            else None
        ),
        raw_call_start_time=call_start,
        call_end_time=parse_datetime(row.get("通话结束时间")),
        transcript=transcript,
        agent_id=text_or_none(row.get("坐席工号")),
        agent_name=text_or_none(row.get("坐席姓名")),
        business_content=text_or_none(row.get("业务内容")),
        answer_content=text_or_none(row.get("答复内容")),
        recording_path=text_or_none(row.get("录音路径")),
        registration_unit=text_or_none(row.get("登记单位")),
        handling_method=text_or_none(row.get("登记处理方式")),
        business_category=text_or_none(row.get("业务类别")),
        satisfaction=text_or_none(row.get("满意度")),
        call_serial_number=text_or_none(row.get("呼叫流水号")),
        core_question=text_or_none(row.get("大模型核心问题")),
        topic_category=text_or_none(row.get("一级专题类别")),
        demand_category=(
            text_or_none(row.get("需求类别")) if trust_analyzed_fields else None
        ),
        father_question=(
            text_or_none(row.get("father_question")) if trust_analyzed_fields else None
        ),
        father_question_2=(
            text_or_none(row.get("father_question_2"))
            if trust_analyzed_fields
            else None
        ),
        caller_type=caller_type,
        enterprise_identity=enterprise_identity,
        resolved_status=(resolved if unresolved is None else not unresolved),
        unresolved_reason=None,
        work_order=determine_work_order(
            row.get("是否工单") if trust_analyzed_fields else None,
            row.get("登记处理方式"),
        ),
        rule_abnormal_end=(
            normalize_boolean(row.get("非正常中断"))
            if trust_analyzed_fields
            and normalize_boolean(row.get("非正常中断")) is not None
            else determine_rule_abnormal_end(
                transcript, row.get("业务内容"), row.get("答复内容")
            )
        ),
        model_abnormal_end=(
            normalize_boolean(row.get("是否非正常中断（大模型判断）"))
            if trust_analyzed_fields
            else None
        ),
        waiting_expression=(
            normalize_boolean(row.get("是否存在让纳税人等待表述"))
            if trust_analyzed_fields
            else None
        ),
        potential_pushback=(
            normalize_boolean(row.get("坐席是否存在潜在推诿行为"))
            if trust_analyzed_fields
            else None
        ),
        taxpayer_dissatisfied=(
            normalize_boolean(row.get("纳税人是否对坐席存在不满"))
            if trust_analyzed_fields
            else None
        ),
        contacted_other_department=(
            normalize_boolean(row.get("是否联系相关人员或部门"))
            if trust_analyzed_fields
            else None
        ),
        active_contacted_other_department=(
            normalize_boolean(row.get("是否主动联系相关人员或部门"))
            if trust_analyzed_fields
            else None
        ),
        contact_target=(
            text_or_none(row.get("联系对象")) if trust_analyzed_fields else None
        ),
        natural_qa_turns=(
            int_or_none(row.get("自然问答轮次")) if trust_analyzed_fields else None
        ),
        core_question_turns=(
            int_or_none(row.get("大模型核心问题轮次"))
            if trust_analyzed_fields
            else None
        ),
        effective_qa_turns=(
            int_or_none(row.get("有效问答轮次")) if trust_analyzed_fields else None
        ),
        effective_qa_content=(
            text_or_none(row.get("有效问答内容")) if trust_analyzed_fields else None
        ),
        raw_identity_label=text_or_none(row.get("申请人员身份")),
        proficiency_score=None,
        proficiency_summary="无法判断",
        proficiency_level=(
            text_or_none(row.get("业务熟悉度")) or "暂无法判断"
            if trust_analyzed_fields
            else "暂无法判断"
        ),
        proficiency_basis=(
            text_or_none(row.get("业务熟悉度依据"))
            or "现有字段未提供业务熟悉度依据。"
            if trust_analyzed_fields
            else "等待分析。"
        ),
        emotion_state=(
            text_or_none(row.get("近期情绪状态")) or "暂无法判断"
            if trust_analyzed_fields
            else "暂无法判断"
        ),
        emotion_basis=(
            text_or_none(row.get("情绪状态依据")) or "现有字段未提供情绪状态依据。"
            if trust_analyzed_fields
            else "等待分析。"
        ),
        service_rating=None,
        service_summary=None,
        enterprise_identity_source=(
            "trusted_source_or_transcript" if trust_analyzed_fields else "unknown"
        ),
        enterprise_identity_conflict=False,
    )

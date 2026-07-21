"""Export profiles, abstract trajectories, and update logs to a new workbook."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment
from sqlalchemy import select

from taxpayer_profile.database import make_engine, make_session_factory
from taxpayer_profile.models import CallerProfile, CallTrajectory, UpdateLog
from taxpayer_profile.security import PhoneProtector


def _boolean(value: bool | None) -> str:
    if value is None:
        return "无法判断"
    return "是" if value else "否"


def _datetime(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


def _excel_safe(value: Any) -> Any:
    """Prevent untrusted text from becoming an Excel formula."""

    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(
        ("=", "+", "-", "@")
    ):
        return "'" + value
    return value


def _safe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {column: _excel_safe(value) for column, value in row.items()} for row in rows
    ]


def _format_workbook(path: Path) -> None:
    workbook = load_workbook(path)
    for worksheet in workbook.worksheets:
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        for row in worksheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for column_cells in worksheet.columns:
            letter = column_cells[0].column_letter
            longest = max(
                (len(str(cell.value)) for cell in column_cells if cell.value is not None),
                default=8,
            )
            worksheet.column_dimensions[letter].width = min(max(longest + 2, 10), 45)
    workbook.save(path)


def export_results(
    *, database_path: Path | str, output_path: Path | str, protector: PhoneProtector
) -> Path:
    destination = Path(output_path).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"拒绝覆盖已有导出文件：{destination}")

    engine = make_engine(database_path)
    sessions = make_session_factory(engine)
    with sessions() as session:
        profiles = list(
            session.scalars(select(CallerProfile).order_by(CallerProfile.latest_call_time))
        )
        trajectories = list(
            session.scalars(select(CallTrajectory).order_by(CallTrajectory.call_time))
        )
        logs = list(session.scalars(select(UpdateLog).order_by(UpdateLog.started_at)))

    phones = {
        profile.phone_hash: protector.decrypt_phone(profile.phone_encrypted)
        for profile in profiles
    }
    profile_rows: list[dict[str, Any]] = [
        {
            "来电号码": phones[profile.phone_hash],
            "最近主体": profile.caller_type,
            "最近企业身份": profile.enterprise_identity,
            "办税熟练程度": profile.proficiency_score,
            "熟练程度说明": profile.proficiency_summary,
            "首次来电时间": _datetime(profile.first_call_time),
            "最近来电时间": _datetime(profile.latest_call_time),
            "累计来电次数": profile.total_call_count,
            "重复来电次数": profile.repeated_call_count,
            "重复问题次数": profile.repeated_issue_count,
            "未直接解决次数": profile.unresolved_count,
            "工单次数": profile.work_order_count,
            "非正常中断次数": profile.abnormal_end_count,
            "不满次数": profile.dissatisfaction_count,
            "最近核心问题": profile.latest_question,
            "最近上层问题": profile.latest_father_question,
            "最近是否解决": _boolean(profile.latest_resolved),
            "最近服务评价": profile.latest_service_rating,
            "画像摘要": profile.profile_summary,
            "近期问题摘要": profile.recent_questions_summary,
            "未解决问题摘要": profile.unresolved_questions_summary,
            "重复问题摘要": profile.repeated_questions_summary,
            "画像更新时间": _datetime(profile.updated_at),
        }
        for profile in profiles
    ]
    trajectory_rows: list[dict[str, Any]] = [
        {
            "业务编号": item.business_id,
            "来电号码": phones.get(item.phone_hash, ""),
            "来电时间": _datetime(item.call_time),
            "登记时间": _datetime(item.registration_time),
            "通话结束时间": _datetime(item.call_end_time),
            "来电时间来源": item.call_time_source,
            "主体": item.caller_type,
            "企业身份": item.enterprise_identity,
            "核心问题": item.core_question,
            "father_question": item.father_question,
            "father_question_2": item.father_question_2,
            "是否直接解决": _boolean(item.resolved_status),
            "是否工单": _boolean(item.work_order),
            "规则非正常中断": _boolean(item.rule_abnormal_end),
            "语义非正常中断": _boolean(item.model_abnormal_end),
            "是否等待": _boolean(item.waiting_expression),
            "是否潜在推诿": _boolean(item.potential_pushback),
            "是否不满": _boolean(item.taxpayer_dissatisfied),
            "是否联系其他人员或部门": _boolean(item.contacted_other_department),
            "是否主动联系": _boolean(item.active_contacted_other_department),
            "联系对象": item.contact_target,
            "自然问答轮次": item.natural_qa_turns,
            "核心问题轮次": item.core_question_turns,
            "有效问答轮次": item.effective_qa_turns,
            "有效问答内容": item.effective_qa_content,
            "办税熟练程度": item.proficiency_score,
            "熟练程度说明": item.proficiency_summary,
            "服务评价": item.service_rating,
            "服务评价说明": item.service_summary,
            "是否重复来电": _boolean(item.is_repeated_call),
            "上次来电时间": _datetime(item.previous_call_time),
            "距上次来电秒数": item.call_interval,
            "历史累计来电次数": item.historical_call_count,
            "是否重复问题": _boolean(item.is_repeated_issue),
            "匹配历史业务编号": item.matched_previous_business_id,
            "匹配历史核心问题": item.matched_previous_question,
            "匹配历史咨询时间": _datetime(item.matched_previous_call_time),
            "前次是否直接解决": _boolean(item.previous_issue_resolved),
            "重复咨询原因": item.repeat_reason,
            "重复问题说明": item.repeat_summary,
            "本地候选相似度": item.repeat_candidate_score,
            "重复问题置信度": item.repeat_confidence,
            "重复问题判断状态": item.repeat_review_status,
            "分析状态": item.analysis_status,
            "分析版本": item.analysis_version,
            "输入模式": item.input_mode,
            "分析来源": item.analysis_source,
            "模型名称": item.model_name,
            "提示词版本": item.prompt_version,
            "抽取版本": item.extraction_version,
            "身份来源": item.enterprise_identity_source,
            "身份冲突": _boolean(item.enterprise_identity_conflict),
            "来源文件": item.source_filename,
        }
        for item in trajectories
    ]
    log_rows: list[dict[str, Any]] = [
        {
            "批次编号": item.batch_id,
            "数据日期": item.data_date,
            "输入文件名": item.input_filename,
            "输入文件指纹": item.input_fingerprint,
            "输入模式": item.input_mode,
            "分析版本": item.analysis_version,
            "源数据行数": item.source_row_count,
            "开始时间": _datetime(item.started_at),
            "结束时间": _datetime(item.finished_at),
            "新增来电": item.new_call_count,
            "新增号码": item.new_phone_count,
            "更新画像": item.updated_profile_count,
            "重复来电": item.repeated_call_count,
            "重复问题": item.repeated_issue_count,
            "未直接解决": item.unresolved_count,
            "失败记录": item.failed_count,
            "跳过记录": item.skipped_count,
            "冲突记录": item.conflict_count,
            "状态": item.status,
            "摘要": item.summary,
        }
        for item in logs
    ]

    destination.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(destination, engine="openpyxl") as writer:
        pd.DataFrame(_safe_rows(profile_rows)).to_excel(
            writer, sheet_name="号码画像", index=False
        )
        pd.DataFrame(_safe_rows(trajectory_rows)).to_excel(
            writer, sheet_name="来电轨迹", index=False
        )
        pd.DataFrame(_safe_rows(log_rows)).to_excel(
            writer, sheet_name="更新摘要", index=False
        )
    _format_workbook(destination)
    return destination

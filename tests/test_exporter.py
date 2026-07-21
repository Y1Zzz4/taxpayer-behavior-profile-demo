from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet
from openpyxl import load_workbook

from taxpayer_profile.config import Settings
from taxpayer_profile.database import (
    create_schema,
    make_engine,
    make_session_factory,
    transactional_session,
)
from taxpayer_profile.exporter import export_results
from taxpayer_profile.models import CallerProfile, CallTrajectory, UpdateLog
from taxpayer_profile.query import query_profile
from taxpayer_profile.security import PhoneProtector
from taxpayer_profile.web_app import DemoService


def _seed_database(path: Path, protector: PhoneProtector) -> None:
    engine = make_engine(path)
    create_schema(engine)
    sessions = make_session_factory(engine)
    phone_hash = protector.hash_phone("13800000001")
    call_time = datetime(2026, 6, 1, 9, 0)
    with transactional_session(sessions) as session:
        session.add(
            CallerProfile(
                phone_hash=phone_hash,
                phone_encrypted=protector.encrypt_phone("13800000001"),
                caller_type="个人",
                enterprise_identity="不适用",
                first_call_time=call_time,
                latest_call_time=call_time,
                total_call_count=1,
                latest_business_id="BIZ-1",
                latest_question='=HYPERLINK("https://example.invalid","申报问题")',
                latest_resolved=True,
                latest_service_rating="良好",
            )
        )
        session.add(
            CallTrajectory(
                business_id="BIZ-1",
                phone_hash=phone_hash,
                call_time=call_time,
                caller_type="个人",
                enterprise_identity="不适用",
                core_question='=HYPERLINK("https://example.invalid","申报问题")',
                resolved_status=True,
                work_order=False,
                rule_abnormal_end=False,
                is_repeated_call=False,
                historical_call_count=1,
                is_repeated_issue=False,
                service_rating="良好",
                analysis_status="completed",
                analysis_version="test-v1",
            )
        )
        session.add(
            UpdateLog(
                batch_id="batch-1",
                data_date="2026-06-01..2026-06-09",
                input_filename="synthetic.xlsx",
                started_at=call_time,
                finished_at=call_time,
                new_call_count=1,
                new_phone_count=1,
                updated_profile_count=1,
                repeated_call_count=0,
                repeated_issue_count=0,
                unresolved_count=0,
                failed_count=0,
                status="completed",
            )
        )


def test_export_has_three_formatted_worksheets_and_query_interface(
    tmp_path: Path,
) -> None:
    database = tmp_path / "profiles.sqlite3"
    output = tmp_path / "results.xlsx"
    protector = PhoneProtector("test-hash-key", Fernet.generate_key().decode())
    _seed_database(database, protector)

    export_results(database_path=database, output_path=output, protector=protector)
    workbook = load_workbook(output)

    assert workbook.sheetnames == ["号码画像", "来电轨迹", "更新摘要"]
    for sheet in workbook.worksheets:
        assert sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref is not None
    assert workbook["号码画像"]["A2"].value == "13800000001"
    trajectory_sheet = workbook["来电轨迹"]
    trajectory_headers = [cell.value for cell in trajectory_sheet[1]]
    resolved_column = trajectory_headers.index("是否直接解决") + 1
    assert trajectory_sheet.cell(row=2, column=resolved_column).value == "是"
    profile_sheet = workbook["号码画像"]
    profile_headers = [cell.value for cell in profile_sheet[1]]
    latest_question_column = profile_headers.index("最近核心问题") + 1
    latest_question_cell = profile_sheet.cell(row=2, column=latest_question_column)
    assert latest_question_cell.data_type == "s"
    assert latest_question_cell.value.startswith("'=")
    question_column = trajectory_headers.index("核心问题") + 1
    question_cell = trajectory_sheet.cell(row=2, column=question_column)
    assert question_cell.data_type == "s"
    assert question_cell.value.startswith("'=")

    result = query_profile(
        phone="13800000001", database_path=database, protector=protector
    )
    assert result is not None
    assert result["total_call_count"] == 1
    assert result["agent_context"]["statistics"]["total_calls"] == 1
    assert "recommended_mode" not in result["agent_context"]
    assert "service_suggestion" not in result
    assert result["trajectories"][0]["business_id"] == "BIZ-1"


def test_web_dashboard_and_history_are_read_only_and_mask_phone(
    tmp_path: Path,
) -> None:
    database = tmp_path / "profiles.sqlite3"
    protector = PhoneProtector("test-hash-key", Fernet.generate_key().decode())
    _seed_database(database, protector)
    settings = Settings(
        database_path=database,
        llm_base_url=None,
        llm_api_key=None,
        llm_model=None,
        phone_hash_key="test-hash-key",
        phone_encryption_key=None,
    )
    service = DemoService(database, protector, settings)

    dashboard = service.dashboard_summary()
    assert dashboard["overview"]["total_profiles"] == 1
    assert dashboard["overview"]["total_calls"] == 1
    assert dashboard["overview"]["resolved_rate"] == 100.0
    assert dashboard["caller_types"] == [{"label": "个人", "value": 1}]
    assert len(dashboard["daily_calls"]) == 14
    assert dashboard["daily_calls"][-1]["value"] == 1
    assert dashboard["resolution_status"] == [
        {"label": "已直接解决", "value": 1}
    ]
    assert dashboard["data_quality"][0]["value"] == 100.0
    assert dashboard["service_signals"][0]["value"] == 0
    assert dashboard["latest_update"]["input_filename"] == "synthetic.xlsx"

    history = service.history_page(page=1, page_size=10, phone="13800000001")
    assert history["filtered"] is True
    assert history["total"] == 1
    assert history["total_pages"] == 1
    assert history["items"][0]["masked_phone"] == "138****0001"
    assert "13800000001" not in str(history)

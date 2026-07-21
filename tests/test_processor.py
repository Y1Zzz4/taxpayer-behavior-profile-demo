from datetime import date, datetime
from pathlib import Path

from cryptography.fernet import Fernet
import pandas as pd
from sqlalchemy import func, select

from taxpayer_profile.database import make_engine, make_session_factory
from taxpayer_profile.models import CallerProfile, CallTrajectory
from taxpayer_profile.processor import process_workbook
from taxpayer_profile.security import PhoneProtector


def _write_calls(path: Path) -> None:
    pd.DataFrame(
        {
            "业务编号": ["BIZ-1", "BIZ-2"],
            "来电号码": ["13800000001", "13800000001"],
            "登记日期": ["2026/6/1 09:00", "2026/6/2 10:00"],
            "通话开始时间": ["2026/6/1 09:00", "2026/6/2 10:00"],
            "通话结束时间": ["2026/6/1 09:10", "2026/6/2 10:10"],
            "转写结果": [
                "纳税人咨询电子税务局申报步骤，坐席进行了说明，谢谢。",
                "我是办税员，继续咨询电子税务局申报步骤，坐席进行了说明，谢谢。",
            ],
            "业务内容": ["咨询申报步骤", "继续咨询申报步骤"],
            "答复内容": ["给出操作路径", "再次给出操作路径"],
            "大模型核心问题": ["电子税务局申报步骤", "电子税务局申报步骤"],
            "father_question": ["申报操作", "申报操作"],
            "father_question_2": [None, None],
            "咨询主体(大模型判断)": ["个人", "企业"],
            "申请人员身份": ["zrr", "bsry"],
            "是否未直接解决问题": [True, False],
            "是否工单": [False, False],
            "登记处理方式": ["1001", "1001"],
            "是否存在让纳税人等待表述": [False, False],
            "坐席是否存在潜在推诿行为": [False, False],
            "纳税人是否对坐席存在不满": [False, False],
            "是否非正常中断（大模型判断）": [False, False],
            "是否联系相关人员或部门": [False, False],
            "有效问答轮次": [2, 3],
            "有效问答内容": ["询问并获得步骤", "基于答复继续确认"],
        }
    ).to_excel(path, index=False)


def test_processing_is_idempotent_and_updates_latest_profile(tmp_path: Path) -> None:
    workbook = tmp_path / "calls.xlsx"
    database = tmp_path / "profiles.sqlite3"
    _write_calls(workbook)
    protector = PhoneProtector("test-hash-key", Fernet.generate_key().decode())

    first = process_workbook(
        input_path=workbook,
        database_path=database,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 9),
        protector=protector,
    )
    second = process_workbook(
        input_path=workbook,
        database_path=database,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 9),
        protector=protector,
    )

    assert first.new_call_count == 2
    assert first.repeated_call_count == 1
    assert first.repeated_issue_count == 0
    assert second.new_call_count == 0
    assert second.skipped_call_count == 2

    engine = make_engine(database)
    sessions = make_session_factory(engine)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(CallTrajectory)) == 2
        profile = session.scalar(select(CallerProfile))
        assert profile is not None
        assert profile.total_call_count == 2
        assert profile.caller_type == "企业"
        assert profile.enterprise_identity == "办税人员"
        assert profile.latest_business_id == "BIZ-2"
        assert profile.repeated_call_count == 1
        assert profile.unresolved_count == 1
        assert profile.profile_summary is not None
        assert "业务熟悉度" in profile.profile_summary
        assert "近期情绪状态" in profile.profile_summary
        assert profile.service_profile_type is None
        assert "最近关注" in profile.profile_summary
        assert profile.proficiency_level in {"专业", "了解", "小白", "暂无法判断"}
        assert profile.emotion_state in {"平稳", "焦虑", "不满", "暂无法判断"}
        assert "电子税务局申报步骤" in (profile.recent_questions_summary or "")
        assert "电子税务局申报步骤" in (profile.unresolved_questions_summary or "")
        assert not hasattr(profile, "recommended_mode")
        assert not hasattr(profile, "service_suggestion")
        second_call = session.get(CallTrajectory, "BIZ-2")
        assert second_call is not None
        assert second_call.is_repeated_issue is None
        assert second_call.repeat_review_status == "pending_review"
        assert second_call.repeat_confidence is None
        assert second_call.repeat_candidate_score == 1.0
        assert second_call.registration_time == datetime(2026, 6, 2, 10)
        assert second_call.call_end_time == datetime(2026, 6, 2, 10, 10)
        assert second_call.call_time_source == "call_start"
        assert second_call.effective_qa_turns == 3
        assert second_call.effective_qa_content == "基于答复继续确认"


def test_backfill_resequences_existing_later_call(tmp_path: Path) -> None:
    database = tmp_path / "profiles.sqlite3"
    later = tmp_path / "later.xlsx"
    earlier = tmp_path / "earlier.xlsx"
    common = {
        "来电号码": ["13800000001"],
        "通话结束时间": [None],
        "答复内容": ["已说明"],
        "咨询主体(大模型判断)": ["个人"],
        "申请人员身份": ["zrr"],
        "是否未直接解决问题": [False],
        "登记处理方式": ["1001"],
    }
    pd.DataFrame(
        {
            **common,
            "业务编号": ["LATER"],
            "登记日期": ["2026/6/10 10:00"],
            "通话开始时间": ["2026/6/10 10:00"],
            "转写结果": ["咨询企业注销，好的。"],
            "业务内容": ["咨询企业注销"],
            "大模型核心问题": ["企业注销"],
        }
    ).to_excel(later, index=False)
    pd.DataFrame(
        {
            **common,
            "业务编号": ["EARLIER"],
            "登记日期": ["2026/6/9 09:00"],
            "通话开始时间": ["2026/6/9 09:00"],
            "转写结果": ["咨询发票领用，好的。"],
            "业务内容": ["咨询发票领用"],
            "大模型核心问题": ["发票领用"],
        }
    ).to_excel(earlier, index=False)
    protector = PhoneProtector("test-hash-key", Fernet.generate_key().decode())

    process_workbook(input_path=later, database_path=database, protector=protector)
    process_workbook(input_path=earlier, database_path=database, protector=protector)

    sessions = make_session_factory(make_engine(database))
    with sessions() as session:
        earlier_call = session.get(CallTrajectory, "EARLIER")
        later_call = session.get(CallTrajectory, "LATER")
        profile = session.scalar(select(CallerProfile))
        assert earlier_call is not None and later_call is not None
        assert earlier_call.is_repeated_call is False
        assert earlier_call.previous_call_time is None
        assert earlier_call.historical_call_count == 1
        assert later_call.is_repeated_call is True
        assert later_call.previous_call_time == earlier_call.call_time
        assert later_call.call_interval == 90000
        assert later_call.historical_call_count == 2
        assert profile is not None
        assert profile.latest_business_id == "LATER"
        assert profile.repeated_call_count == 1

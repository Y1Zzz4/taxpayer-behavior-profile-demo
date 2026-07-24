from datetime import date
from pathlib import Path
import threading
import time

from cryptography.fernet import Fernet
import pandas as pd
from sqlalchemy import select

from taxpayer_profile.database import make_engine, make_session_factory
from taxpayer_profile.excel_reader import InputMode, discover_workbooks
from taxpayer_profile.ingestion.contracts import InputSourceIdentity
from taxpayer_profile.llm_client import (
    CallExtractionResult,
    HistoryEnrichmentResult,
    RepeatIssueModelResult,
)
from taxpayer_profile.models import CallTrajectory, UpdateLog
from taxpayer_profile.processor import process_workbook
from taxpayer_profile.security import PhoneProtector


class FakeAnalysisClient:
    model = "fake-model"

    def __init__(self) -> None:
        self.payloads: list[dict[str, str | None]] = []
        self.history_payloads: list[dict[str, str | None]] = []

    def analyze_call(self, payload: dict[str, str | None]) -> CallExtractionResult:
        self.payloads.append(payload)
        is_raw = payload["business_content"] == "RAW"
        return CallExtractionResult(
            core_question="新抽取核心问题" if is_raw else "模型不应覆盖可信问题",
            father_question="新父问题",
            father_question_2="新二级父问题",
            agent_answer_summary="坐席说明了后续处理路径和等待节点。",
            demand_categories=["操作辅导类"],
            caller_type="企业",
            explicit_enterprise_identity="无法判断",
            model_abnormal_end=False,
            waiting_expression=False,
            potential_pushback=False,
            taxpayer_dissatisfied=False,
            contacted_other_department=False,
            contact_target=None,
            active_contacted_other_department=False,
            resolved_status=False if is_raw else True,
            unresolved_reason="需等待后续处理" if is_raw else None,
            natural_qa_turns=2,
            core_question_turns=1,
            effective_qa_turns=1,
            effective_qa_content="问：问题；答：路径",
            proficiency_score=7,
            proficiency_summary="能够说明具体办理事项。",
            service_rating="一般" if is_raw else "良好",
            service_summary="给出了相关处理路径。",
        )

    def analyze_history(
        self, payload: dict[str, str | None]
    ) -> HistoryEnrichmentResult:
        self.payloads.append(payload)
        self.history_payloads.append(payload)
        return HistoryEnrichmentResult(
            core_question="模型不应覆盖可信问题",
            father_question="模型不应覆盖可信父问题",
            father_question_2="历史事项",
            agent_answer_summary="坐席提炼了历史答复和办理路径。",
            demand_categories=["操作辅导类"],
            caller_type="企业",
            explicit_enterprise_identity="无法判断",
            resolved_status=True,
            unresolved_reason=None,
            proficiency_score=7,
            proficiency_summary="能够说明具体办理事项。",
            proficiency_level="了解",
            proficiency_basis="能够说明业务事项，但仍需确认办理路径。",
            emotion_state="平稳",
            emotion_basis="表达有序且正常确认答复。",
            service_rating="良好",
            service_summary="答复相关并给出处理路径。",
        )

    def analyze_repeat_issue(self, payload):  # pragma: no cover - no ambiguous case here
        raise AssertionError("该测试不应调用重复问题模型")


def _protector() -> PhoneProtector:
    return PhoneProtector("test-hash-key", Fernet.generate_key().decode())


def test_raw_mode_reuses_direct_core_question_but_reanalyzes_other_fields(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "new.xlsx"
    database = tmp_path / "profiles.sqlite3"
    pd.DataFrame(
        {
            "业务编号": ["RAW-1"],
            "来电号码": ["13800000001"],
            "登记日期": ["2026/6/10 09:00"],
            "通话开始时间": ["2026/6/10 09:00"],
            "转写结果": ["我是公司的财务人员，咨询申报，通话正常结束谢谢。"],
            "业务内容": ["RAW"],
            "答复内容": ["说明了后续处理路径"],
            "登记处理方式": ["1404"],
            "申请人员身份": ["cwfzr"],
            "大模型核心问题": ["输入直用核心问题"],
            "一级专题类别": ["增值税申报"],
            "二级标签": ["增值税申报-一般纳税人申报"],
            "father_question": ["必须忽略的旧父问题"],
            "咨询主体(大模型判断)": ["个人"],
            "是否未直接解决问题": [False],
            "是否存在让纳税人等待表述": [True],
            "坐席是否存在潜在推诿行为": [True],
        }
    ).to_excel(workbook, index=False)
    client = FakeAnalysisClient()

    summary = process_workbook(
        input_path=workbook,
        database_path=database,
        protector=_protector(),
        llm_client=client,
    )

    assert summary.new_call_count == 1
    assert client.payloads == [
        {
            "transcript": "我是公司的财务人员，咨询申报，通话正常结束谢谢。",
            "business_content": "RAW",
            "answer_content": "说明了后续处理路径",
            "core_question": "输入直用核心问题",
            "topic_category": "增值税申报",
        }
    ]
    sessions = make_session_factory(make_engine(database))
    with sessions() as session:
        trajectory = session.scalar(select(CallTrajectory))
        assert trajectory is not None
        assert trajectory.core_question == "输入直用核心问题"
        assert trajectory.topic_category == "增值税申报"
        assert trajectory.secondary_topic == "增值税申报-一般纳税人申报"
        assert trajectory.demand_category == "操作辅导类"
        assert trajectory.agent_answer_summary == "坐席说明了后续处理路径和等待节点。"
        assert trajectory.unresolved_reason == "需等待后续处理"
        assert trajectory.father_question == "新父问题"
        assert trajectory.caller_type == "企业"
        assert trajectory.enterprise_identity == "财务负责人"
        assert trajectory.enterprise_identity_source == "source_label"
        assert trajectory.resolved_status is False
        assert trajectory.waiting_expression is False
        assert trajectory.potential_pushback is False
        assert trajectory.work_order is True
        assert trajectory.input_mode == "raw_analysis"
        assert trajectory.analysis_source == "model+rules"


def test_bootstrap_mixed_keeps_trusted_fields_and_can_model_enrich_history(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "bootstrap.xlsx"
    database = tmp_path / "profiles.sqlite3"
    pd.DataFrame(
        {
            "业务编号": ["HISTORY-1", "RAW-1"],
            "来电号码": ["13800000001", "13800000002"],
            "登记日期": ["2026/6/9 09:00", "2026/6/10 09:00"],
            "通话开始时间": ["2026/6/9 09:00", "2026/6/10 09:00"],
            "转写结果": ["历史转写，谢谢。", "新增转写，谢谢。"],
            "业务内容": ["HISTORY", "RAW"],
            "答复内容": ["历史答复", "新增答复"],
            "登记处理方式": ["1001", "1001"],
            "申请人员身份": ["zrr", "cwfzr"],
            "大模型核心问题": ["可信历史核心问题", "不可信6月10日问题"],
            "father_question": ["可信历史父问题", "不可信父问题"],
            "咨询主体(大模型判断)": ["个人", "个人"],
            "是否未直接解决问题": [False, False],
        }
    ).to_excel(workbook, index=False)

    client = FakeAnalysisClient()
    process_workbook(
        input_path=workbook,
        database_path=database,
        protector=_protector(),
        llm_client=client,
        input_mode=InputMode.BOOTSTRAP_MIXED,
        trusted_through=date(2026, 6, 9),
    )

    sessions = make_session_factory(make_engine(database))
    with sessions() as session:
        trajectories = {
            item.business_id: item for item in session.scalars(select(CallTrajectory))
        }
        history = trajectories["HISTORY-1"]
        raw = trajectories["RAW-1"]
        assert history.core_question == "可信历史核心问题"
        assert history.father_question == "可信历史父问题"
        assert history.caller_type == "个人"
        assert history.resolved_status is True
        assert history.agent_answer_summary == "坐席提炼了历史答复和办理路径。"
        assert history.input_mode == "trusted_import"
        assert raw.core_question == "不可信6月10日问题"
        assert raw.caller_type == "企业"
        assert raw.resolved_status is False
        assert raw.input_mode == "raw_analysis"
        assert len(client.payloads) == 2
        assert len(client.history_payloads) == 1
        assert [item["business_content"] for item in client.payloads] == [
            "HISTORY",
            "RAW",
        ]


def test_model_extraction_cache_resumes_without_repeating_successful_call(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "cached.xlsx"
    cache = tmp_path / "model-cache.sqlite3"
    pd.DataFrame(
        {
            "业务编号": ["CACHE-1"],
            "来电号码": ["13800000001"],
            "登记日期": ["2026/6/10 09:00"],
            "转写结果": ["咨询申报操作。"],
            "业务内容": ["RAW"],
            "答复内容": ["说明操作路径。"],
        }
    ).to_excel(workbook, index=False)

    first_client = FakeAnalysisClient()
    process_workbook(
        input_path=workbook,
        database_path=tmp_path / "first.sqlite3",
        protector=_protector(),
        llm_client=first_client,
        input_mode=InputMode.RAW_ANALYSIS,
        model_workers=2,
        extraction_cache_path=cache,
    )
    second_client = FakeAnalysisClient()
    process_workbook(
        input_path=workbook,
        database_path=tmp_path / "second.sqlite3",
        protector=_protector(),
        llm_client=second_client,
        input_mode=InputMode.RAW_ANALYSIS,
        model_workers=2,
        extraction_cache_path=cache,
    )

    assert len(first_client.payloads) == 1
    assert second_client.payloads == []
    assert b"13800000001" not in cache.read_bytes()


def test_model_extraction_uses_bounded_parallel_workers(tmp_path: Path) -> None:
    workbook = tmp_path / "parallel.xlsx"
    rows = 6
    pd.DataFrame(
        {
            "业务编号": [f"P-{index}" for index in range(rows)],
            "来电号码": [f"138000000{index + 1:02d}" for index in range(rows)],
            "登记日期": [f"2026/6/10 {9 + index:02d}:00" for index in range(rows)],
            "转写结果": ["咨询申报操作。"] * rows,
            "业务内容": ["RAW"] * rows,
            "答复内容": ["说明操作路径。"] * rows,
        }
    ).to_excel(workbook, index=False)

    class TrackingClient(FakeAnalysisClient):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.peak = 0
            self.lock = threading.Lock()

        def analyze_call(self, payload):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                time.sleep(0.03)
                return super().analyze_call(payload)
            finally:
                with self.lock:
                    self.active -= 1

    client = TrackingClient()
    process_workbook(
        input_path=workbook,
        database_path=tmp_path / "parallel.sqlite3",
        protector=_protector(),
        llm_client=client,
        input_mode=InputMode.RAW_ANALYSIS,
        model_workers=3,
    )

    assert client.peak == 3


def test_repeat_model_reviews_are_parallel_and_resume_from_cache(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "repeat-parallel.xlsx"
    cache = tmp_path / "repeat-cache.sqlite3"
    phone_numbers = [f"138000000{index + 1:02d}" for index in range(4)]
    pd.DataFrame(
        {
            "业务编号": [f"R-{index}" for index in range(8)],
            "来电号码": phone_numbers + phone_numbers,
            "登记日期": [
                f"2026/6/10 {9 + index:02d}:00" for index in range(8)
            ],
            "转写结果": ["继续咨询同一申报问题。"] * 8,
            "业务内容": ["RAW"] * 8,
            "答复内容": ["说明操作路径。"] * 8,
        }
    ).to_excel(workbook, index=False)

    class RepeatTrackingClient(FakeAnalysisClient):
        def __init__(self) -> None:
            super().__init__()
            self.repeat_calls = 0
            self.repeat_active = 0
            self.repeat_peak = 0
            self.lock = threading.Lock()

        def analyze_repeat_issue(self, payload):
            with self.lock:
                self.repeat_calls += 1
                self.repeat_active += 1
                self.repeat_peak = max(self.repeat_peak, self.repeat_active)
            try:
                time.sleep(0.03)
                return RepeatIssueModelResult(
                    is_repeated_issue=False,
                    matched_history_index=None,
                    repeat_reason="无法判断",
                    explanation="语义复核后未确认属于重复诉求。",
                    confidence=0.8,
                )
            finally:
                with self.lock:
                    self.repeat_active -= 1

    first_client = RepeatTrackingClient()
    process_workbook(
        input_path=workbook,
        database_path=tmp_path / "repeat-first.sqlite3",
        protector=_protector(),
        llm_client=first_client,
        input_mode=InputMode.RAW_ANALYSIS,
        model_workers=3,
        extraction_cache_path=cache,
    )
    second_client = RepeatTrackingClient()
    process_workbook(
        input_path=workbook,
        database_path=tmp_path / "repeat-second.sqlite3",
        protector=_protector(),
        llm_client=second_client,
        input_mode=InputMode.RAW_ANALYSIS,
        model_workers=3,
        extraction_cache_path=cache,
    )

    assert first_client.repeat_calls == 4
    assert first_client.repeat_peak == 3
    assert second_client.payloads == []
    assert second_client.repeat_calls == 0


def test_workbook_discovery_ignores_temporary_and_non_excel_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "b.xlsx").touch()
    (tmp_path / "a.xlsx").touch()
    (tmp_path / "~$a.xlsx").touch()
    (tmp_path / "notes.txt").touch()
    assert [path.name for path in discover_workbooks(tmp_path)] == ["a.xlsx", "b.xlsx"]


def test_three_consecutive_model_failures_abort_without_database_writes(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "unavailable.xlsx"
    database = tmp_path / "profiles.sqlite3"
    pd.DataFrame(
        {
            "业务编号": ["B1", "B2", "B3"],
            "来电号码": ["13800000001", "13800000002", "13800000003"],
            "登记日期": ["2026/6/10 09:00", "2026/6/10 10:00", "2026/6/10 11:00"],
            "通话开始时间": ["2026/6/10 09:00", "2026/6/10 10:00", "2026/6/10 11:00"],
            "转写结果": ["有效转写一", "有效转写二", "有效转写三"],
            "业务内容": ["问题一", "问题二", "问题三"],
            "答复内容": ["答复一", "答复二", "答复三"],
        }
    ).to_excel(workbook, index=False)

    class UnavailableClient(FakeAnalysisClient):
        def analyze_call(self, payload):
            raise RuntimeError("service unavailable")

    import pytest

    with pytest.raises(RuntimeError, match="熔断"):
        process_workbook(
            input_path=workbook,
            database_path=database,
            protector=_protector(),
            llm_client=UnavailableClient(),
            input_mode=InputMode.RAW_ANALYSIS,
        )

    sessions = make_session_factory(make_engine(database))
    with sessions() as session:
        assert list(session.scalars(select(CallTrajectory))) == []
        assert list(session.scalars(select(UpdateLog))) == []


def test_application_accepts_non_excel_tabular_adapter_and_skips_repeat_read(
    tmp_path: Path,
) -> None:
    database = tmp_path / "profiles.sqlite3"
    source = tmp_path / "future-input.table"

    class MemoryAdapter:
        def __init__(self) -> None:
            self.read_count = 0

        def identify(self, input_source: Path) -> InputSourceIdentity:
            assert input_source == source.resolve()
            return InputSourceIdentity(
                name="memory-batch-1",
                fingerprint="a" * 64,
            )

        def read_rows(
            self,
            input_source: Path,
            *,
            start_date: date | None = None,
            end_date: date | None = None,
        ) -> list[dict[str, object]]:
            assert input_source == source.resolve()
            assert start_date is None
            assert end_date is None
            self.read_count += 1
            return [
                {
                    "业务编号": "MEM-1",
                    "来电号码": "13800000008",
                    "登记日期": "2026/7/24 10:00",
                    "通话开始时间": "2026/7/24 10:00",
                    "转写结果": "企业财务人员咨询增值税申报操作。",
                    "业务内容": "咨询增值税申报",
                    "答复内容": "说明了线上申报操作步骤。",
                    "大模型核心问题": "增值税如何申报",
                    "一级专题类别": "增值税申报",
                    "二级标签": "增值税申报-一般纳税人申报",
                    "申请人员身份": "企业财务人员",
                }
            ]

    adapter = MemoryAdapter()
    first = process_workbook(
        input_path=source,
        database_path=database,
        protector=_protector(),
        input_mode=InputMode.TRUSTED_IMPORT,
        input_adapter=adapter,
    )
    second = process_workbook(
        input_path=source,
        database_path=database,
        protector=_protector(),
        input_mode=InputMode.TRUSTED_IMPORT,
        input_adapter=adapter,
    )

    assert first.input_filename == "memory-batch-1"
    assert first.new_call_count == 1
    assert second.already_processed is True
    assert adapter.read_count == 1
    sessions = make_session_factory(make_engine(database))
    with sessions() as session:
        trajectory = session.get(CallTrajectory, "MEM-1")
        assert trajectory is not None
        assert trajectory.source_filename == "memory-batch-1"
        assert trajectory.core_question == "增值税如何申报"

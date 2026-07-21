"""Minimal OpenAI-compatible client with strict validated JSON output."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from taxpayer_profile.config import PROJECT_ROOT
from taxpayer_profile.security import redact_sensitive_text

RepeatReason = Literal[
    "前次未解决",
    "前次答复未被理解",
    "等待审核或处理结果",
    "同一事项继续追问",
    "已解决后再次确认",
    "无法判断",
]
CallerType = Literal["个人", "企业", "无法判断"]
EnterpriseIdentity = Literal[
    "法定代表人", "财务负责人", "办税人员", "其他", "无法判断", "不适用"
]
ServiceRating = Literal["良好", "一般", "需关注", "无法判断"]
ProficiencyLevel = Literal["专业", "了解", "小白", "暂无法判断"]
EmotionState = Literal["平稳", "焦虑", "不满", "暂无法判断"]
DemandCategory = Literal[
    "政策咨询类",
    "操作辅导类",
    "工单/拉起类",
    "涉税查询类",
    "系统异常类",
    "投诉举报类",
    "意见建议类",
    "其他类",
]
PROMPT_VERSION = "call-extraction-v8"
REPEAT_PROMPT_VERSION = "repeat-issue-v4"
REALTIME_ADVICE_PROMPT_VERSION = "realtime-service-advice-v6"

AdviceText = Annotated[str, Field(min_length=1, max_length=300)]


class CallExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    core_question: str | None = Field(default=None, max_length=300)
    father_question: str | None = Field(default=None, max_length=300)
    father_question_2: str | None = Field(default=None, max_length=300)
    demand_categories: list[DemandCategory] = Field(min_length=1, max_length=2)
    caller_type: CallerType
    explicit_enterprise_identity: EnterpriseIdentity
    model_abnormal_end: bool | None
    waiting_expression: bool | None
    potential_pushback: bool | None
    taxpayer_dissatisfied: bool | None
    contacted_other_department: bool | None
    contact_target: str | None = Field(default=None, max_length=200)
    active_contacted_other_department: bool | None
    resolved_status: bool | None
    unresolved_reason: str | None = Field(default=None, max_length=300)
    natural_qa_turns: int | None = Field(default=None, ge=0)
    core_question_turns: int | None = Field(default=None, ge=0)
    effective_qa_turns: int | None = Field(default=None, ge=0)
    effective_qa_content: str | None = Field(default=None, max_length=2000)
    proficiency_score: float | None = Field(default=None, ge=1, le=10)
    proficiency_summary: str = Field(min_length=1, max_length=200)
    proficiency_level: ProficiencyLevel = "暂无法判断"
    proficiency_basis: str = Field(
        default="现有结果未提供业务熟悉度依据。", min_length=1, max_length=300
    )
    emotion_state: EmotionState = "暂无法判断"
    emotion_basis: str = Field(
        default="现有结果未提供情绪状态依据。", min_length=1, max_length=300
    )
    service_rating: ServiceRating
    service_summary: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_consistency(self) -> "CallExtractionResult":
        if self.proficiency_score is None and self.proficiency_summary != "无法判断":
            raise ValueError("熟练度为空时说明必须为“无法判断”")
        if self.proficiency_level == "暂无法判断" and self.proficiency_score is not None:
            self.proficiency_level = (
                "专业"
                if self.proficiency_score >= 8
                else "了解"
                if self.proficiency_score >= 5
                else "小白"
            )
        if (
            self.natural_qa_turns is not None
            and self.effective_qa_turns is not None
            and self.effective_qa_turns > self.natural_qa_turns
        ):
            raise ValueError("有效问答轮次不能大于自然问答轮次")
        if self.resolved_status is False and not self.unresolved_reason:
            raise ValueError("未直接解决时必须提供简要原因")
        if self.resolved_status is not False and self.unresolved_reason is not None:
            raise ValueError("直接解决或无法判断时未解决原因必须为空")
        return self


class RepeatIssueModelResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_repeated_issue: bool
    matched_history_index: int | None = Field(default=None, ge=0)
    repeat_reason: RepeatReason
    explanation: str = Field(min_length=1, max_length=120)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_match(self) -> "RepeatIssueModelResult":
        if self.is_repeated_issue and self.matched_history_index is None:
            raise ValueError("重复问题必须提供 matched_history_index")
        if not self.is_repeated_issue and self.matched_history_index is not None:
            raise ValueError("非重复问题不得提供 matched_history_index")
        return self


class RealtimeServiceAdviceResult(BaseModel):
    """Structured phone-level service advice; never a policy answer."""

    model_config = ConfigDict(extra="forbid")

    advice_summary: str = Field(min_length=1, max_length=200)
    service_mode: str = Field(min_length=1, max_length=80)
    mode_application: str = Field(
        min_length=1,
        max_length=300,
        description="说明推荐模式如何具体约束本次接待方式，不得改写推荐模式",
    )
    service_focus: list[AdviceText] = Field(default_factory=list, max_length=4)
    opening_strategy: str = Field(min_length=1, max_length=300)
    communication_style: str = Field(min_length=1, max_length=300)
    history_followups: list[AdviceText] = Field(default_factory=list, max_length=5)
    risk_reminders: list[AdviceText] = Field(default_factory=list, max_length=5)
    avoid_actions: list[AdviceText] = Field(default_factory=list, max_length=5)
    # Accepted for compatibility with an older prompt, but not exposed by the UI.
    recommended_sequence: list[AdviceText] = Field(default_factory=list, max_length=6)
    evidence: list[AdviceText] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def fill_macro_focus(self) -> "RealtimeServiceAdviceResult":
        if not self.service_focus:
            self.service_focus = [self.opening_strategy]
        return self


def build_repeat_payload(
    *,
    core_question: str | None,
    father_question: str | None,
    father_question_2: str | None,
    business_content: str | None,
    histories: list[object],
) -> dict[str, object]:
    """Build an explicit privacy-whitelisted model payload."""

    def value(item: object, field: str) -> object:
        if isinstance(item, dict):
            return item.get(field)
        return getattr(item, field, None)

    anonymous_history = []
    for index, history in enumerate(histories):
        call_time = value(history, "call_time")
        anonymous_history.append(
            {
                "history_index": index,
                "call_date": (
                    call_time.date().isoformat()
                    if isinstance(call_time, datetime)
                    else None
                ),
                "core_question": redact_sensitive_text(value(history, "core_question")),
                "father_question": redact_sensitive_text(
                    value(history, "father_question")
                ),
                "father_question_2": redact_sensitive_text(
                    value(history, "father_question_2")
                ),
                "resolved": value(history, "resolved_status"),
            }
        )
    return {
        "current": {
            "core_question": redact_sensitive_text(core_question),
            "father_question": redact_sensitive_text(father_question),
            "father_question_2": redact_sensitive_text(father_question_2),
            "business_content": redact_sensitive_text(business_content),
        },
        "history": anonymous_history,
    }


def build_call_payload(
    *,
    transcript: str | None,
    business_content: str | None,
    answer_content: str | None,
    core_question: str | None,
    topic_category: str | None,
) -> dict[str, str | None]:
    """Build a privacy-minimized payload for call extraction and classification."""

    return {
        "transcript": redact_sensitive_text(transcript),
        "business_content": redact_sensitive_text(business_content),
        "answer_content": redact_sensitive_text(answer_content),
        "core_question": redact_sensitive_text(core_question),
        "topic_category": redact_sensitive_text(topic_category),
    }


ModelResult = TypeVar("ModelResult", bound=BaseModel)


@dataclass
class OpenAICompatibleClient:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 45.0
    max_attempts: int = 3
    prompt_path: Path = PROJECT_ROOT / "prompts/repeat_issue_system.txt"
    extraction_prompt_path: Path = PROJECT_ROOT / "prompts/call_extraction_system.txt"
    advice_prompt_path: Path = PROJECT_ROOT / "prompts/realtime_service_advice_system.txt"

    def _request_json(
        self,
        *,
        payload: dict[str, object] | dict[str, str | None],
        prompt_path: Path,
        result_type: type[ModelResult],
        max_tokens: int,
    ) -> ModelResult:
        system_prompt = prompt_path.read_text(encoding="utf-8")
        system_prompt += "\n\n必须符合以下 JSON Schema：\n"
        system_prompt += json.dumps(result_type.model_json_schema(), ensure_ascii=False)
        request_body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        # DeepSeek V4 enables reasoning by default. Structured extraction and
        # real-time service advice need bounded latency, not a long CoT phase.
        if self.model.casefold().startswith("deepseek-v4"):
            request_body["thinking"] = {"type": "disabled"}
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        timeout = httpx.Timeout(
            connect=min(self.timeout_seconds, 5.0),
            read=self.timeout_seconds,
            write=min(self.timeout_seconds, 10.0),
            pool=min(self.timeout_seconds, 5.0),
        )
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                response = httpx.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=request_body,
                    timeout=timeout,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return result_type.model_validate_json(content)
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    time.sleep(2**attempt)
        raise RuntimeError(f"模型分析失败：{result_type.__name__}") from last_error

    def analyze_call(self, payload: dict[str, str | None]) -> CallExtractionResult:
        return self._request_json(
            payload=payload,
            prompt_path=self.extraction_prompt_path,
            result_type=CallExtractionResult,
            max_tokens=4096,
        )

    def analyze_repeat_issue(self, payload: dict[str, object]) -> RepeatIssueModelResult:
        return self._request_json(
            payload=payload,
            prompt_path=self.prompt_path,
            result_type=RepeatIssueModelResult,
            max_tokens=768,
        )

    def generate_service_advice(
        self, payload: dict[str, object]
    ) -> RealtimeServiceAdviceResult:
        return self._request_json(
            payload=payload,
            prompt_path=self.advice_prompt_path,
            result_type=RealtimeServiceAdviceResult,
            max_tokens=1536,
        )

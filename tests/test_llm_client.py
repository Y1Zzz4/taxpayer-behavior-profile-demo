from datetime import datetime
import json

import httpx

from taxpayer_profile.llm_client import (
    CallExtractionResult,
    OpenAICompatibleClient,
    build_call_payload,
    build_repeat_payload,
)


def test_repeat_payload_contains_only_whitelisted_anonymous_fields() -> None:
    payload = build_repeat_payload(
        core_question="申报问题",
        father_question="申报",
        father_question_2=None,
        business_content="咨询操作",
        histories=[
            {
                "business_id": "MUST-NOT-LEAK",
                "phone": "13800000001",
                "call_time": datetime(2026, 6, 1),
                "core_question": "历史申报问题",
                "father_question": "申报",
                "father_question_2": None,
                "resolved_status": False,
            }
        ],
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert "MUST-NOT-LEAK" not in rendered
    assert "13800000001" not in rendered
    assert set(payload) == {"current", "history"}
    assert set(payload["history"][0]) == {  # type: ignore[index]
        "history_index",
        "call_date",
        "core_question",
        "father_question",
        "father_question_2",
        "resolved",
    }


def test_call_extraction_payload_includes_direct_business_fields() -> None:
    payload = build_call_payload(
        transcript="转写文本",
        business_content="业务内容",
        answer_content="答复内容",
        core_question="核心问题",
        topic_category="专题类别",
    )
    assert payload == {
        "transcript": "转写文本",
        "business_content": "业务内容",
        "answer_content": "答复内容",
        "core_question": "核心问题",
        "topic_category": "专题类别",
    }


def test_model_payloads_redact_common_identifiers_before_sending() -> None:
    private_text = (
        "手机号13800000001，身份证11010519491231002X，"
        "邮箱demo@example.com，账号6222020202020202020，"
        "统一社会信用代码91350100M000100Y43"
    )
    call_payload = build_call_payload(
        transcript=private_text,
        business_content="联系010-12345678",
        answer_content=None,
        core_question=private_text,
        topic_category=None,
    )
    repeat_payload = build_repeat_payload(
        core_question=private_text,
        father_question=None,
        father_question_2=None,
        business_content="联系010-12345678",
        histories=[
            {
                "call_time": datetime(2026, 6, 1),
                "core_question": "历史号码13900000002",
                "father_question": None,
                "father_question_2": None,
                "resolved_status": False,
            }
        ],
    )
    rendered = json.dumps(
        {"call": call_payload, "repeat": repeat_payload}, ensure_ascii=False
    )

    for private_value in (
        "13800000001",
        "13900000002",
        "11010519491231002X",
        "demo@example.com",
        "6222020202020202020",
        "91350100M000100Y43",
        "010-12345678",
    ):
        assert private_value not in rendered
    assert "<手机号>" in rendered
    assert "<身份证号>" in rendered
    assert "<邮箱>" in rendered
    assert "<账号>" in rendered
    assert "<社会信用代码>" in rendered
    assert "<固定电话>" in rendered


def test_client_uses_temperature_zero_and_validates_json(
    monkeypatch, tmp_path
) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("test prompt", encoding="utf-8")
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs["json"])
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "is_repeated_issue": True,
                                    "matched_history_index": 0,
                                        "repeat_reason": "同一事项继续追问",
                                        "explanation": "当前问题与历史事项一致。",
                                        "confidence": 0.93,
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = OpenAICompatibleClient(
        "https://example.invalid/v1", "secret", "model", prompt_path=prompt
    )

    result = client.analyze_repeat_issue({"current": {}, "history": []})

    assert captured["temperature"] == 0
    assert captured["response_format"] == {"type": "json_object"}
    assert result.is_repeated_issue is True
    assert result.confidence == 0.93


def test_call_extraction_schema_rejects_inconsistent_proficiency() -> None:
    valid = {
        "core_question": "如何申报",
        "father_question": "申报",
        "father_question_2": None,
        "agent_answer_summary": "坐席说明了申报操作路径。",
        "demand_categories": ["操作辅导类"],
        "caller_type": "企业",
        "explicit_enterprise_identity": "无法判断",
        "model_abnormal_end": False,
        "waiting_expression": False,
        "potential_pushback": False,
        "taxpayer_dissatisfied": False,
        "contacted_other_department": False,
        "contact_target": None,
        "active_contacted_other_department": False,
        "resolved_status": True,
        "unresolved_reason": None,
        "natural_qa_turns": 1,
        "core_question_turns": 1,
        "effective_qa_turns": 1,
        "effective_qa_content": "问：如何申报；答：说明操作路径",
        "proficiency_score": None,
        "proficiency_summary": "无法判断",
        "service_rating": "良好",
        "service_summary": "答复与问题相关并给出操作路径。",
    }
    result = CallExtractionResult.model_validate(valid)
    assert result.proficiency_score is None


def test_deepseek_v4_disables_thinking_and_bounds_output(
    monkeypatch, tmp_path
) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("test prompt", encoding="utf-8")
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs["json"])
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "is_repeated_issue": False,
                                    "matched_history_index": None,
                                    "repeat_reason": "无法判断",
                                    "explanation": "没有可匹配的历史事项。",
                                    "confidence": 0.9,
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = OpenAICompatibleClient(
        "https://example.invalid", "secret", "DeepSeek-V4-Pro", prompt_path=prompt
    )

    client.analyze_repeat_issue({"current": {}, "history": []})

    assert captured["thinking"] == {"type": "disabled"}
    assert captured["max_tokens"] == 768

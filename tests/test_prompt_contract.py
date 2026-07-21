from pathlib import Path

from taxpayer_profile.config import PROJECT_ROOT
from taxpayer_profile.llm_client import (
    PROMPT_VERSION,
    REALTIME_ADVICE_PROMPT_VERSION,
    REPEAT_PROMPT_VERSION,
)


def test_call_extraction_prompt_contains_required_business_rules() -> None:
    prompt_path = PROJECT_ROOT / "prompts/call_extraction_system.txt"
    prompt = prompt_path.read_text(encoding="utf-8")

    required_rules = {
        "input_priority": "business_content 和 answer_content 是人工登记的主证据",
        "transcript_detail": "transcript 是对话细节证据",
        "conflict_rule": "三者发生明确冲突时",
        "multi_issue_priority": "core_question 必须优先对应 business_content",
        "contact_targets": "专管员、办税大厅、出口退税所、主管税务所、征纳互动",
        "active_contact": "active_contacted_other_department",
        "resolved": "是否直接解决",
        "semantic_abnormal": "语义非正常中断",
        "waiting": "等待和潜在推诿",
        "dissatisfaction_scope": "只判断纳税人是否对当前坐席或本通热线服务表达不满",
        "effective_qa": "effective_qa_content",
        "caller_type": "咨询主体",
        "identity": "转写中明确的企业身份",
        "proficiency": "办税熟练程度",
        "service": "服务效果评估",
        "demand_category": "工单/拉起类",
        "unresolved_reason": "unresolved_reason",
        "specific_core_issue": "问题发生的业务场景 + 来电人真正要解决的事项",
        "no_policy_claim": "不判断政策答案是否绝对正确",
    }
    missing = [name for name, text in required_rules.items() if text not in prompt]
    assert missing == []
    assert "近期情绪状态" in prompt
    assert "业务熟悉度" in prompt
    assert "专业" in prompt and "了解" in prompt and "小白" in prompt
    assert PROMPT_VERSION == "call-extraction-v8"


def test_prompt_does_not_request_prohibited_structured_identifiers() -> None:
    prompt = (PROJECT_ROOT / "prompts/call_extraction_system.txt").read_text(
        encoding="utf-8"
    )
    prohibited = ["业务编号：", "来电号码：", "坐席工号：", "纳税人名称："]
    assert all(item not in prompt for item in prohibited)


def test_realtime_advice_prompt_is_phone_level_not_a_policy_answer() -> None:
    prompt = (
        PROJECT_ROOT / "prompts/realtime_service_advice_system.txt"
    ).read_text(encoding="utf-8")

    assert "不能回答任何具体税务、政策、材料或办理问题" in prompt
    assert "不能直接认定为本次来电目的" in prompt
    assert "advice_summary" in prompt
    assert "号码或其他个人标识" in prompt
    assert "recommended_mode 由本地确定性规则生成" in prompt
    assert "required_mode_contract" in prompt
    assert "mode_application" in prompt
    assert "接待模式含义" in prompt
    assert "不得写成带序号的逐步执行流程" in prompt
    assert "不要自行添加" in prompt
    assert REALTIME_ADVICE_PROMPT_VERSION == "realtime-service-advice-v6"


def test_repeat_prompt_separates_contact_queries_from_business_repetition() -> None:
    prompt = (PROJECT_ROOT / "prompts/repeat_issue_system.txt").read_text(
        encoding="utf-8"
    )

    assert "通用联络诉求" in prompt
    assert "降低 confidence" in prompt
    assert REPEAT_PROMPT_VERSION == "repeat-issue-v4"

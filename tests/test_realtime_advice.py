from taxpayer_profile.config import PROJECT_ROOT
from taxpayer_profile.llm_client import RealtimeServiceAdviceResult
from taxpayer_profile.realtime_advice import (
    build_fallback_advice,
    generate_realtime_advice,
)


def _context() -> dict[str, object]:
    return {
        "profile_summary": "累计来电2次，存在一条未解决事项。",
        "caller_type": "企业",
        "enterprise_identity": "办税人员",
        "proficiency_score": 8.5,
        "proficiency_summary": "能够准确描述系统环节。",
        "statistics": {
            "total_calls": 2,
            "repeated_calls": 1,
            "repeated_issues": 0,
            "unresolved_calls": 1,
            "work_orders": 0,
            "abnormal_ends": 0,
            "dissatisfaction_calls": 0,
        },
        "latest_resolved": False,
        "recent_trajectories": [
            {
                "question": "手机号13800000001对应的社保欠费缴纳",
                "question_category": "社保费业务",
                "resolved": False,
                "potential_pushback": False,
            }
        ],
    }


class FakeAdviceClient:
    model = "fake-model"

    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None

    def generate_service_advice(
        self, payload: dict[str, object]
    ) -> RealtimeServiceAdviceResult:
        self.payload = payload
        return RealtimeServiceAdviceResult(
            advice_summary="历史存在一项未解决记录，建议先确认是否延续该事项，再衔接后续处理节点。",
            service_mode="历史衔接核对型",
            opening_strategy="先确认本次是否延续历史事项。",
            communication_style="采用简洁专业的表达。",
            history_followups=["核对历史未解决事项。"],
            risk_reminders=[],
            avoid_actions=["不要预设本次诉求。"],
            recommended_sequence=["确认本次诉求。", "按本次问题提供服务。"],
            evidence=["历史存在一条未解决记录。"],
        )


def test_realtime_model_advice_is_structured_and_context_is_redacted() -> None:
    client = FakeAdviceClient()
    result = generate_realtime_advice(_context(), client)

    assert result["generation_status"] == "model_generated"
    assert "未解决" in result["advice_summary"]
    assert result["service_mode"] == "历史衔接核对型"
    assert result["model_name"] == "fake-model"
    assert client.payload is not None
    assert "13800000001" not in str(client.payload)
    assert "<手机号>" in str(client.payload)


def test_realtime_advice_uses_fast_rule_fallback() -> None:
    result = build_fallback_advice(_context(), fallback_reason="model_ReadTimeout")

    assert result["generation_status"] == "rules_fallback"
    assert result["fallback_reason"] == "model_ReadTimeout"
    assert result["service_mode"] == "未解决事项进度闭环型"
    assert "历史来电2次" in result["advice_summary"]
    assert "不要把历史问题直接当成本次来电问题" in result["avoid_actions"][0]
    assert result["recommended_sequence"]


def test_web_ui_prioritizes_12366_summary_and_collapsible_details() -> None:
    page = (PROJECT_ROOT / "web/index.html").read_text(encoding="utf-8")

    for required in (
        "12366",
        "纳税缴费服务热线",
        "涉税费咨询、信息查询、服务投诉、违法举报、意见建议",
        "来电服务工作台",
        "画像数据概览",
        "来电量趋势",
        "问题解决情况",
        "业务熟练度分层",
        "数据覆盖质量",
        "数据解读",
        "历史来电记录",
        "接听前速览",
        "advice.advice_summary",
        "优先执行事项",
        "查看详细接待建议",
        "查看全部来电",
        "上一页",
        "下一页",
    ):
        assert required in page
    assert "radial-gradient" not in page
    assert "本地 Demo 在线" not in page

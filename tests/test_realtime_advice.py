from taxpayer_profile.config import PROJECT_ROOT
from taxpayer_profile.llm_client import RealtimeServiceAdviceResult
from taxpayer_profile.realtime_advice import (
    build_fallback_advice,
    generate_realtime_advice,
)
from taxpayer_profile.web_app import REALTIME_ADVICE_TIMEOUT_SECONDS


def _context() -> dict[str, object]:
    return {
        "profile_summary": "累计来电2次，存在一条未解决事项。",
        "caller_type": "企业",
        "enterprise_identity": "办税人员",
        "proficiency_level": "了解",
        "proficiency_basis": "能够准确描述系统环节。",
        "emotion_state": "平稳",
        "emotion_basis": "表达有序。",
        "recommended_mode": "问题跟进",
        "mode_basis": "近五个工作日存在联系后未解决1次。",
        "mode_guidance": {
            "focus": "优先核验历史事项当前状态。",
            "communication": "确认后从历史节点继续。",
            "avoid": "避免重复登记。",
        },
        "recent_five_workdays": {
            "work_order_count": 0,
            "wait_pushback_count": 0,
            "abnormal_end_count": 0,
            "contact_unresolved_count": 1,
            "dissatisfaction_count": 0,
        },
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
            mode_application="先确认历史事项是否仍在处理，再从已有节点继续衔接。",
            opening_strategy="先确认本次是否延续历史事项。",
            communication_style="采用简洁专业的表达。",
            history_followups=["核对历史未解决事项。"],
            risk_reminders=[],
            avoid_actions=["不要预设本次诉求。"],
            recommended_sequence=["1. 确认本次诉求。", "2.1. 按本次问题提供服务。"],
            evidence=["历史存在一条未解决记录。"],
        )


def test_realtime_model_advice_is_structured_and_context_is_redacted() -> None:
    client = FakeAdviceClient()
    result = generate_realtime_advice(_context(), client)

    assert result["generation_status"] == "model_generated"
    assert "未解决" in result["advice_summary"]
    assert result["advice_summary"].startswith("推荐采用“问题跟进”")
    assert result["service_mode"] == "问题跟进"
    assert result["mode_application"].startswith("采用“问题跟进”")
    assert result["model_name"] == "fake-model"
    assert "recommended_sequence" not in result
    assert result["service_focus"][0] == "优先核验历史事项当前状态。"
    assert result["communication_style"].startswith("确认后从历史节点继续。")
    assert result["avoid_actions"][0] == "避免重复登记。"
    assert result["evidence"][0] == "近五个工作日存在联系后未解决1次。"
    assert client.payload is not None
    assert "13800000001" not in str(client.payload)
    assert "<手机号>" in str(client.payload)
    assert client.payload["required_mode_contract"] == {
        "service_mode": "问题跟进",
        "selection_basis": "近五个工作日存在联系后未解决1次。",
        "required_focus": "优先核验历史事项当前状态。",
        "required_communication": "确认后从历史节点继续。",
        "required_avoid": "避免重复登记。",
    }


def test_realtime_advice_uses_fast_rule_fallback() -> None:
    result = build_fallback_advice(_context(), fallback_reason="model_ReadTimeout")

    assert result["generation_status"] == "rules_fallback"
    assert result["fallback_reason"] == "model_ReadTimeout"
    assert result["service_mode"] == "问题跟进"
    assert result["mode_application"].startswith("采用“问题跟进”")
    assert "历史来电2次" in result["advice_summary"]
    assert "避免重复登记" in result["avoid_actions"][0]
    assert result["service_focus"]


def test_realtime_advice_timeout_allows_normal_model_latency_variation() -> None:
    assert REALTIME_ADVICE_TIMEOUT_SECONDS == 25.0


def test_web_ui_prioritizes_12366_summary_and_collapsible_details() -> None:
    page = (PROJECT_ROOT / "web/index.html").read_text(encoding="utf-8")
    page += (PROJECT_ROOT / "web/app.js").read_text(encoding="utf-8")

    for required in (
        "12366",
        "纳税服务热线",
        "12366坐席接待助手",
        "画像数据概览",
        "来电量趋势",
        "问题解决情况",
        "历史服务事实",
        "专题类别与解决情况",
        "需求类别与解决情况",
        "历史来电记录",
        "画像推演中心",
        "历史问题衔接",
        "同类诉求",
        "等待推诿",
        "近期画像依据",
        "画像证据回放",
        "增量画像结果",
        "增量画像推演",
        "多维画像图谱",
        "整体画像逻辑",
        "号码画像实例",
        "knowledge-profile-select",
        "当前展示整体画像方法论，不关联具体号码",
        "多维画像三维关系图",
        "拖拽旋转",
        "knowledge-graph-canvas",
        "完整分类与判定规则",
        "三维画像字段",
        "四种坐席接待模式",
        "业务熟悉度",
        "近期情绪状态",
        "/api/showcase",
        "信息速览",
        "近5个工作日",
        "人工登记与原始信息",
        "重点分析信息",
        "advice.advice_summary",
        "接待重点",
        "查看全部来电",
        "上一页",
        "下一页",
        "用户与权限",
        "renderStacked",
    ):
        assert required in page
    assert '<span class="panel-icon">' not in page
    assert '<h2>服务画像分类方法</h2>' not in page
    assert "requestAnimationFrame" in page
    assert "本地 Demo 在线" not in page
    assert "profileBox.append(profileTypeButton" not in page
    assert "appendInfo(primary" not in page
    assert "item.label" in page

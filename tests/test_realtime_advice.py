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
        "recommended_mode": "重点解释 · 平稳接待 · 历史诉求跟进",
        "mode_basis": "表达方式、情绪响应和业务应对分别判断后组合。",
        "mode_guidance": {
            "focus": "保持平稳沟通；核验历史事项；解释关键条件。",
            "communication": "正常确认情绪；从历史节点继续；围绕重点解释。",
            "avoid": "避免放大风险、重复登记或说明过度。",
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
    assert not result["advice_summary"].startswith("推荐采用“")
    assert result["service_mode"] == "重点解释 · 平稳接待 · 历史诉求跟进"
    assert "情绪响应采用“平稳接待”" in result["mode_application"]
    assert [item["mode"] for item in result["service_modes"]] == [
        "重点解释",
        "平稳接待",
        "历史诉求跟进",
    ]
    assert result["model_name"] == "fake-model"
    assert "recommended_sequence" not in result
    assert result["service_focus"][0].startswith("先说明关键判断")
    assert result["service_focus"][1].startswith("保持清晰、自然")
    assert result["service_focus"][2].startswith("先确认本次是否延续历史事项")
    assert result["communication_style"].startswith("使用适量业务术语")
    assert result["avoid_actions"][0].startswith("避免过度简化关键条件")
    assert result["evidence"][0].startswith("业务专业度为了解")
    assert client.payload is not None
    assert "13800000001" not in str(client.payload)
    assert "<手机号>" in str(client.payload)
    contract = client.payload["required_mode_contract"]
    assert contract["service_mode"] == "重点解释 · 平稳接待 · 历史诉求跟进"
    assert len(contract["components"]) == 3
    assert len(contract["required_focuses"]) == 3


def test_realtime_advice_uses_fast_rule_fallback() -> None:
    result = build_fallback_advice(_context(), fallback_reason="model_ReadTimeout")

    assert result["generation_status"] == "rules_fallback"
    assert result["fallback_reason"] == "model_ReadTimeout"
    assert result["service_mode"] == "重点解释 · 平稳接待 · 历史诉求跟进"
    assert "业务应对采用“历史诉求跟进”" in result["mode_application"]
    assert "历史来电2次" in result["advice_summary"]
    assert any("避免重复登记" in item for item in result["avoid_actions"])
    assert result["service_focus"]


def test_realtime_advice_timeout_allows_normal_model_latency_variation() -> None:
    assert REALTIME_ADVICE_TIMEOUT_SECONDS == 25.0


def test_web_ui_prioritizes_12366_summary_and_explainable_derivation() -> None:
    html = (PROJECT_ROOT / "web/index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "web/app.js").read_text(encoding="utf-8")
    page = html + script

    # The browser must exercise the same JavaScript that this contract checks.
    # A previous text/plain copy in index.html made stale UI assertions pass
    # even though that implementation was never executed.
    assert html.count('<script src="/app.js"></script>') == 1
    assert 'type="text/plain"' not in html

    for required in (
        "12366",
        "纳税服务热线",
        "12366坐席接待助手",
        "热线数据概览",
        "来电量趋势",
        "咨询主体构成",
        "不同咨询主体解决率",
        "登记单位服务效果",
        "历史服务事实",
        "未直接解决问题",
        "专题类别与未直接解决率",
        "需求类别与未直接解决率",
        "历史来电记录",
        "画像推演中心",
        "未解决问题衔接",
        "重复诉求",
        "存在联系相关部门或人员且未解决",
        "该号码全部历史来电",
        "组合接待策略",
        "总体接待建议",
        "整体画像逻辑",
        "号码画像实例",
        "knowledge-profile-select",
        "当前展示整体画像方法论，不关联具体号码",
        "多维画像三维关系图",
        "拖拽旋转",
        "knowledge-graph-canvas",
        "完整分类与判定规则",
        "纳税人画像字段",
        "坐席接待方式",
        "情绪响应",
        "业务应对",
        "表达方式",
        "mode_emotion_response",
        "mode_matter_continuity",
        "mode_information_delivery",
        "groupSources",
        "categoryEdgeKeys",
        "分层推导舞台",
        "纳税人信息",
        "isModeGroup ? progress.category",
        "isMode ? progress.mode",
        "advice.service_modes",
            "业务专业度",
            "近期情绪",
        "/api/showcase",
        "信息速览",
            "近5个工作日",
        "人工登记与原始信息",
        "重点分析信息",
        "advice.advice_summary",
        "查看全部来电信息",
        "caller-history-overlay",
        "上一页",
        "下一页",
        "用户与权限",
        "caller_resolution_rates",
        "unresolved_question_hotspots",
        "renderVerticalRateBars",
        "renderCallerResolutionComparison",
        "renderUnresolvedRateDistribution",
    ):
        assert required in page
    assert "画像数据概览" not in page
    assert "本次推导结果卡" not in page
    assert '<span class="panel-icon">' not in page
    assert '<h2>服务画像分类方法</h2>' not in page
    assert "requestAnimationFrame" in page
    assert "本地 Demo 在线" not in page
    assert "profileBox.append(profileTypeButton" not in page
    assert "appendInfo(primary" not in page
    assert "item.label" in page
    assert "增量画像推演" not in page


def test_history_issue_narrative_normalizes_source_sentence_marks() -> None:
    script = (PROJECT_ROOT / "web/app.js").read_text(encoding="utf-8")

    # Individual model/manual clauses are normalized before they are joined,
    # preventing combinations such as “；。” when a reason already has “。”.
    assert "function formatIssueNarrative(fragments)" in script
    assert "replace(/[；;。！？!?]+([”’）】\\]]*)$/u, '$1')" in script
    assert "formatIssueNarrative(facts)" in script

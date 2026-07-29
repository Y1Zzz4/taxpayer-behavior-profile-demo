from taxpayer_profile.profiling import RECEPTION_MODE_CATALOG, classify_reception_mode


def test_reception_modes_select_one_component_from_each_category() -> None:
    professional_emotional = classify_reception_mode(
        proficiency_level="专业",
        emotion_state="不满",
        work_order_count=1,
    )
    anxious_informed = classify_reception_mode(
        proficiency_level="了解", emotion_state="焦虑"
    )
    guided = classify_reception_mode(
        proficiency_level="小白", emotion_state="平稳", abnormal_end_count=1
    )

    assert professional_emotional.mode == "结论直述 · 安抚修复 · 历史诉求跟进"
    assert [item.mode for item in professional_emotional.components] == [
        "结论直述",
        "安抚修复",
        "历史诉求跟进",
    ]
    assert anxious_informed.mode == "重点解释 · 稳定预期 · 当前诉求确认"
    assert guided.mode == "通俗引导 · 平稳接待 · 历史诉求跟进"
    assert all(len(result.components) == 3 for result in (
        professional_emotional,
        anxious_informed,
        guided,
    ))
    assert len(RECEPTION_MODE_CATALOG) == 8
    assert all(not item["label"].endswith("型") for item in RECEPTION_MODE_CATALOG)


def test_mode_category_priority_does_not_cover_other_categories() -> None:
    result = classify_reception_mode(
        proficiency_level="专业",
        emotion_state="焦虑",
        wait_pushback_count=1,
        contact_unresolved_count=1,
    )

    assert [item.mode for item in result.components] == [
        "结论直述",
        "安抚修复",
        "历史诉求跟进",
    ]
    assert "等待且潜在推诿1次" in result.matched_facts
    assert "存在联系相关部门或人员且未解决1次" in result.matched_facts

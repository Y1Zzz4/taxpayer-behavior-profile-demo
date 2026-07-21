from taxpayer_profile.profiling import classify_reception_mode


def test_reception_mode_priority_and_labels_do_not_use_type_suffix() -> None:
    soothe = classify_reception_mode(
        proficiency_level="专业",
        emotion_state="平稳",
        work_order_count=1,
        dissatisfaction_count=1,
    )
    followup = classify_reception_mode(
        proficiency_level="专业",
        emotion_state="平稳",
        contact_unresolved_count=1,
    )
    direct = classify_reception_mode(
        proficiency_level="了解", emotion_state="焦虑"
    )
    guided = classify_reception_mode(
        proficiency_level="小白", emotion_state="平稳"
    )

    assert [soothe.mode, followup.mode, direct.mode, guided.mode] == [
        "耐心安抚",
        "问题跟进",
        "结论直给",
        "通俗引导",
    ]
    assert all(not item.mode.endswith("型") for item in (soothe, followup, direct, guided))

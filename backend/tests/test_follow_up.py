from backend.app.application.follow_up import resolve_follow_up, resolve_question


def test_standalone_query_is_unchanged():
    assert resolve_follow_up("科学学习有哪些方法？", "旧问题") == ("科学学习有哪些方法？", False)


def test_referent_compatibility_preserves_q0_and_requires_clarification():
    question, used = resolve_follow_up("这种方法如何应用？", "检索练习是什么？" + "x" * 1500)
    assert (question, used) == ("这种方法如何应用？", False)
    resolution = resolve_question(question, "检索练习是什么？")
    assert resolution.clarification_required and resolution.decision == "clarify"
    assert resolution.query_original == question and not resolution.source_run_ids


def test_no_matching_completed_scope_leaves_q0_usable():
    assert resolve_follow_up("它是什么？", None) == ("它是什么？", False)


def test_explicit_previous_turn_compatibility_never_injects_history():
    assert resolve_follow_up("你刚才列出的最后一项是什么？", "复习间隔有哪些？") == ("你刚才列出的最后一项是什么？", False)
    assert resolve_follow_up("刚才的 E1 怎样回读？", "引用怎么使用？") == ("刚才的 E1 怎样回读？", False)

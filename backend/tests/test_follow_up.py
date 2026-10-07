from backend.app.application.follow_up import resolve_question


def test_standalone_query_is_unchanged():
    q0 = "科学学习有哪些方法？"
    resolution = resolve_question(q0, "旧问题")
    assert resolution.question == resolution.query_original == q0
    assert not resolution.used and not resolution.clarification_required


def test_no_history_keeps_q0_and_clarifies_referent():
    q0 = "它是什么？"
    resolution = resolve_question(q0, None)
    assert resolution.question == resolution.query_original == q0
    assert resolution.clarification_required and resolution.decision == "clarify"
    assert not resolution.used and resolution.source_run_ids == ()

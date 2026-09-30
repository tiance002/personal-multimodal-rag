from backend.app.application.follow_up import resolve_follow_up


def test_standalone_query_is_unchanged():
    assert resolve_follow_up("科学学习有哪些方法？", "旧问题") == ("科学学习有哪些方法？", False)


def test_referent_uses_question_only_bounded_background():
    question, used = resolve_follow_up("这种方法如何应用？", "检索练习是什么？" + "x" * 1500)
    assert used and "只回答当前问题：这种方法如何应用？" in question
    assert len(question) < 1100
    assert "不是证据" in question


def test_no_matching_completed_scope_leaves_q0_usable():
    assert resolve_follow_up("它是什么？", None) == ("它是什么？", False)


def test_explicit_previous_turn_reference_uses_bounded_context():
    assert resolve_follow_up("你刚才列出的最后一项是什么？", "复习间隔有哪些？")[1]
    assert resolve_follow_up("刚才的 E1 怎样回读？", "引用怎么使用？")[1]

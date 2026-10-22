from __future__ import annotations

import pytest

from scripts.s8_6_4_language import expected_profile_language, message_headers, policy_manifest, resolve_request_locale


@pytest.mark.parametrize(("q0", "expected"), [
    ("请解释这个函数，并保留 API 名称", "zh-CN"),
    ("Explain this function and keep API names unchanged.", "en-US"),
    ("Compare Redis 缓存策略 with PostgreSQL persistence.", "zh-CN"),
])
def test_question_script_default_locale(q0, expected):
    assert resolve_request_locale(q0) == expected
    assert expected_profile_language(q0)[0] == expected


@pytest.mark.parametrize(("q0", "override", "expected"), [
    ("请说明 API 行为", "en-US", "en-US"),
    ("Explain API behavior", "zh-CN", "zh-CN"),
    ("请用英文回答：什么是 Redis？", None, "en-US"),
    ("Please answer in French: what is Redis?", None, "fr-FR"),
])
def test_explicit_answer_language_precedes_script_default(q0, override, expected):
    assert resolve_request_locale(q0, override) == expected


def test_unsupported_explicit_locale_fails_closed():
    with pytest.raises(ValueError, match="S864_UNSUPPORTED_EXPLICIT_LANGUAGE"):
        resolve_request_locale("请解释", "xx-ZZ")


def test_manifest_describes_locale_precedence_and_limits():
    policy = policy_manifest()
    assert policy["precedence"] == [
        "explicit_answer_language", "explicit_q0_answer_directive", "q0_script_default"
    ]
    assert policy["default"] == {"contains_han": "zh-CN", "otherwise": "en-US"}


def test_acceptance_profiles_hash_the_language_policy_instead_of_fixed_en_us():
    from scripts.s8_6_1_api_acceptance import profile_manifest as s861_manifest
    from scripts.s8_6_2_r3_40_acceptance import profile_manifest as s862_manifest

    s861 = s861_manifest()
    s861_bounded = s861_manifest(bounded_query_profile=True)
    s862 = s862_manifest()
    assert s861["accept_language_by_case"] == {"compare": "en-US", "dependent": "en-US"}
    assert s861_bounded["accept_language_by_case"] == {"compare": "en-US"}
    assert "accept_language" not in s861 and "Accept-Language" not in s862
    assert s861["answer_language_policy"] == s862["answer_language_policy"] == policy_manifest()


@pytest.mark.parametrize(("module_name", "q0", "expected"), [
    ("scripts.s8_6_1_api_acceptance", "请总结 RAG 检索", "zh-CN"),
    ("scripts.s8_6_1_api_acceptance", "Summarize RAG retrieval", "en-US"),
    ("scripts.s8_6_2_r3_40_acceptance", "请用英文回答：什么是 RAG？", "en-US"),
    ("scripts.s8_6_2_r3_40_acceptance", "What is RAG?", "en-US"),
])
def test_acceptance_driver_request_header_guard(module_name, q0, expected):
    module = __import__(module_name, fromlist=["profile_request_headers"])
    headers = module.profile_request_headers(q0, {"Authorization": "Bearer local-test"})
    assert headers["Accept-Language"] == expected
    assert headers["Authorization"] == "Bearer local-test"


def test_completed_profile_language_guard_compares_expected_locale():
    from scripts.s8_6_1_api_acceptance import profile_language_guard

    assert profile_language_guard("Chinese (Simplified)", "中文问题") == {
        "expected_locale": "zh-CN", "expected_language": "Chinese (Simplified)",
        "actual_language": "Chinese (Simplified)", "matched": True,
    }
    with pytest.raises(ValueError, match="S864_PROFILE_LANGUAGE_MISMATCH"):
        profile_language_guard("English", "中文问题")

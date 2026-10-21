"""Offline S8.1 inspection tests. No network, DB or live authorization."""
import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.weknora_port.models.catalog import resolve_model_config
from backend.weknora_port.models.types import ChatOptions
from scripts.s8_1_ecs_acceptance import contract_probe, inspect, public_config, write_new


def config(provider="siliconflow", model="XingChenAGI/Xing4.0-29B"):
    return resolve_model_config(provider, model, base_url="https://api.siliconflow.cn/v1",
                                model_id="synthetic-inspection-only")


def test_contract_uses_actual_supplied_options_and_builder_without_transport():
    actual = contract_probe(config(), ChatOptions(max_completion_tokens=777,
        temperature=.41, thinking=False, reasoning_effort="off"), stream=True)
    assert actual["status"] == "VERIFIED"
    assert actual["wire_generation_fields"]["max_tokens"] == 777
    assert actual["wire_generation_fields"]["temperature"] == .41
    assert actual["wire_generation_fields"]["enable_thinking"] is False
    assert actual["contract"]["output_upper"] == 777
    assert actual["placeholder_only"] is True
    assert actual["provider_requests"] == 0


def test_missing_thinking_wire_remains_unverifiable():
    base = config()
    unknown = replace(base, chat=replace(base.chat, thinking_format="none"))
    actual = contract_probe(unknown, ChatOptions(max_completion_tokens=777), stream=False)
    assert actual["status"] == "BLOCKED"
    assert actual["error_code"] == "MODEL_OUTPUT_CONTRACT_UNKNOWN"
    assert "contract" not in actual


def test_no_output_limit_is_never_fabricated():
    actual = contract_probe(config(), ChatOptions(thinking=False), stream=True)
    assert actual["status"] == "BLOCKED"
    assert actual["error_code"] == "MODEL_OUTPUT_HARD_LIMIT_DENIED"


def test_public_config_excludes_credential_slots():
    actual = public_config(replace(config(), custom_headers={"Authorization": "secret"},
                                  extra_config={"api_key": "secret"}))
    assert "secret" not in str(actual)
    assert "custom_headers" not in actual
    assert "extra_config" not in actual
    assert "base_url" not in actual


@pytest.mark.parametrize("env", [
    {"WEKNORA_PORT_CLOUD_ALLOWED": "true"},
    {"WEKNORA_PORT_EXECUTION_FILE": "/not-an-active-grant"},
])
def test_inspection_rejects_live_mode_before_assembly(tmp_path, monkeypatch, env):
    import scripts.s8_ecs_runtime as ecs
    def forbidden(*args, **kwargs):
        raise AssertionError("assembly must not run")
    monkeypatch.setattr(ecs, "settings_and_factory", forbidden)
    with pytest.raises(ValueError, match="CLOSED_CLOUD_NO_EXECUTION"):
        asyncio.run(inspect(tmp_path / "result.json", env))
    assert not (tmp_path / "result.json").exists()


def test_evidence_never_overwrites_prior_report(tmp_path):
    output = tmp_path / "inspection.json"
    write_new(output, {"status": "first"})
    with pytest.raises(FileExistsError):
        write_new(output, {"status": "second"})
    assert 'first' in output.read_text()

def test_instance_settings_keep_baseline_closed_and_preserve_defaults(tmp_path):
    import hashlib
    from scripts.s8_1_ecs_acceptance import acceptance_settings
    password = tmp_path / "password"; password.write_text("synthetic")
    principal = tmp_path / "principal.json"
    subject = "p8-real-synthetic-acceptance-only"
    import json
    principal.write_text(json.dumps({"tenant_id": 332, "subject_id": subject,
        "subject_sha256": hashlib.sha256(subject.encode()).hexdigest()}))
    env = {"S8_DB_PASSWORD_FILE": str(password), "S8_PRINCIPAL_FILE": str(principal),
        "WEKNORA_PORT_DATABASE_NAME": "weknora_s8", "WEKNORA_PORT_DATABASE_SYSTEM_ID": "123456",
        "WEKNORA_PORT_REDIS_RUN_ID": "a" * 40, "WEKNORA_PORT_REDIS_PREFIX": "weknora-python-s8:runtime",
        "WEKNORA_PORT_REDIS_URL": "redis://localhost:6379/8",
        "WEKNORA_PORT_STORAGE_ROOT": str(tmp_path), "WEKNORA_PORT_SECRET_KEY_FILE": str(tmp_path / "key")}
    baseline = dict(env)
    closed, who = acceptance_settings(env, "A")
    assert closed.cloud_allowed is False and closed.execution_file is None
    assert closed.graph_enabled is closed.rerank_top1_floorless is False
    scoped, _ = acceptance_settings(env, "B", tmp_path / "synthetic-only-no-grant.json")
    assert scoped.cloud_allowed is True and scoped.rerank_top1_floorless is True
    assert scoped.graph_enabled is False
    assert scoped.query_understand_model_id == closed.query_understand_model_id == ""
    assert scoped.source_evidence_mode == closed.source_evidence_mode == "off"
    assert env == baseline and who.tenant_id == 332
    with pytest.raises(ValueError, match="D_FORBIDDEN"):
        acceptance_settings(env, "D")


def test_retrieval_observer_preserves_original_objects_and_restores_hooks(tmp_path, monkeypatch):
    import json
    from backend.weknora_port.storage import hybrid
    from backend.weknora_port.storage.postgres import PostgresRetriever, RetrieveParams
    from backend.weknora_port.retrieval.search import SearchTarget, SearchParams
    from backend.weknora_port.models.types import CallContext, TrustedScope
    from scripts.s8_1_ecs_acceptance import retrieval_observer
    vector = [{"chunk_id": "vector", "score": .7}]
    keyword = [{"chunk_id": "keyword", "score": .3}]
    final = [{"id": "same-final-object"}]
    async def vectors(self, params): return vector
    async def keywords(self, params): return keyword
    def fusion(v, k, config):
        assert v is vector and k is keyword
        return final
    async def recall(self, targets, params, context, query_embedding=None):
        v = await PostgresRetriever.vectors(None, RetrieveParams(("kb",)))
        k = await PostgresRetriever.keywords(None, RetrieveParams(("kb",)))
        assert hybrid.fuse_or_deduplicate(v, k, None) is final
        return final
    monkeypatch.setattr(PostgresRetriever, "vectors", vectors)
    monkeypatch.setattr(PostgresRetriever, "keywords", keywords)
    monkeypatch.setattr(hybrid, "fuse_or_deduplicate", fusion)
    monkeypatch.setattr(hybrid.PostgresKnowledgeRepository, "hybrid_search", recall)
    context = CallContext("synthetic-only", TrustedScope("subject", ("kb",)))
    with retrieval_observer(tmp_path):
        actual = asyncio.run(hybrid.PostgresKnowledgeRepository.hybrid_search(
            None, (SearchTarget("kb"),), SearchParams("question"), context))
        assert actual is final
    assert hybrid.PostgresKnowledgeRepository.hybrid_search is recall
    assert PostgresRetriever.vectors is vectors
    assert hybrid.fuse_or_deduplicate is fusion
    saved = json.loads(next(tmp_path.glob("*.retrieval.json")).read_text())
    assert [c["channel"] for c in saved["channels"]] == ["vector", "bm25"]
    assert saved["fusions"][0]["full_fused_order"] == final
    assert saved["returned_results"] == final


def test_b_uses_completed_a_chain_without_inventing_low_score_requirement(tmp_path):
    from scripts.s8_1_ecs_acceptance import AcceptanceController
    from scripts.s8_evaluation_controller import case_key
    case = {"dataset": "mmlongbench-doc", "qid": "row-0434", "knowledge_base_id": "kb", "document_scope": []}
    controller = object.__new__(AcceptanceController)
    controller.arm, controller.path = "B", tmp_path / "checkpoint.json"
    controller.checkpoint = {"entries": {case_key(case, "A"): {"arm": "A", "status": "COMPLETE"}}}
    parent = {"checkpoint_path": str(controller.path), "max_case_arms": {"A": 9, "B": 2, "C": 2}, "end_to_end_total": 13}
    controller.validate_case_limits(parent, case)
    controller.checkpoint["entries"][case_key(case, "A")]["status"] = "UNKNOWN"
    with pytest.raises(ValueError, match="COMPLETED_A_CHAIN"):
        controller.validate_case_limits(parent, case)


def alignment_fixture():
    from copy import deepcopy
    roles = {role: {"provider": "siliconflow", "model": "Qwen/Qwen3.5-4B" if role == "cheap" else role,
        "source": "remote", "api": "openai-completions", "parameters_sha256": "a" * 64,
        "model_id": "local-" + role} for role in ("cheap", "expensive", "embedding", "rerank", "vlm")}
    local = {"roles": roles, "conversation_settings": {"query_understand_model_id": "local-cheap", "summary": {"temperature": .3}},
        "router_policy": {"mode": "DYNAMIC", "threshold": "0.35"},
        "query_understanding": {"provider": "siliconflow", "model": "Qwen/Qwen3.5-4B", "source": "remote"},
        "effective_generation_options_for_normal_request_off": {"thinking": False},
        "prompt_template_sha256": {"context": "b" * 64}, "intent_system_prompts_sha256": "c" * 64}
    actual = {"models": deepcopy(roles), "conversation_settings": deepcopy(local["conversation_settings"]),
        "router_policy": deepcopy(local["router_policy"]), "effective_generation_options": {"thinking": False},
        "generation_output_contracts": {"cheap": {"status": "VERIFIED"}, "expensive": {"status": "VERIFIED"}},
        "prompt_template_sha256": local["prompt_template_sha256"], "intent_system_prompts_sha256": "c" * 64}
    for row in actual["models"].values(): row["model_id"] = row["model_id"].replace("local-", "ecs-")
    actual["conversation_settings"]["query_understand_model_id"] = "ecs-cheap"
    actual["conversation_settings"]["rerank_top1_floorless"] = True
    actual["query_understanding"] = {"ecs-cheap": {"config": {"provider": "siliconflow", "model": "Qwen/Qwen3.5-4B", "source": "remote"}}}
    return actual, local


def test_alignment_compares_parameters_without_cross_tenant_model_ids():
    from scripts.s8_1_ecs_acceptance import validate_alignment
    actual, local = alignment_fixture()
    assert validate_alignment(actual, local)["status"] == "PASS"
    actual["models"]["cheap"]["parameters_sha256"] = "changed"
    with pytest.raises(ValueError, match="cheap:parameters_sha256"):
        validate_alignment(actual, local)


@pytest.mark.parametrize("field", ["prompt_template_sha256", "router_policy", "effective_generation_options"])
def test_alignment_refuses_template_router_or_generation_drift(field):
    from scripts.s8_1_ecs_acceptance import validate_alignment
    actual, local = alignment_fixture()
    actual[field] = {}
    with pytest.raises(ValueError, match=field): validate_alignment(actual, local)


def test_alignment_requires_actual_explicit_current_qu_identity():
    from scripts.s8_1_ecs_acceptance import validate_alignment
    actual, local = alignment_fixture()
    actual["conversation_settings"]["query_understand_model_id"] = "old-xing-id"
    with pytest.raises(ValueError, match="QU_explicit_current_cheap_identity"):
        validate_alignment(actual, local)


@pytest.mark.parametrize("tools", [[], [{"type": "function", "function": {"name": "lookup", "parameters": {"required": ["id"]}}}]])
def test_alignment_accepts_json_equivalent_empty_and_nonempty_tools_sequences(tools):
    from copy import deepcopy
    from scripts.s8_1_ecs_acceptance import validate_alignment
    actual, local = alignment_fixture()
    local["effective_generation_options_for_normal_request_off"].update(tools=deepcopy(tools), max_completion_tokens=1024)
    actual["effective_generation_options"].update(tools=tuple(deepcopy(tools)), max_completion_tokens=1024)
    assert validate_alignment(actual, local)["status"] == "PASS"
    actual["effective_generation_options"]["max_completion_tokens"] = 1025
    with pytest.raises(ValueError, match="effective_generation_options"):
        validate_alignment(actual, local)


def test_alignment_json_comparison_preserves_nonempty_tool_parameter_differences():
    from copy import deepcopy
    from scripts.s8_1_ecs_acceptance import validate_alignment
    actual, local = alignment_fixture()
    tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"required": ["id"]}}}]
    local["effective_generation_options_for_normal_request_off"]["tools"] = deepcopy(tools)
    actual["effective_generation_options"]["tools"] = tuple(deepcopy(tools))
    actual["effective_generation_options"]["tools"][0]["function"]["parameters"]["required"] = ["other"]
    with pytest.raises(ValueError, match="effective_generation_options"):
        validate_alignment(actual, local)

"""Bounded diagnostic safety tests; no credentials, sockets, PG or real models."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import s8_4_bridge_ecs as runner
from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.bridge_rescue import SYSTEM
from backend.weknora_port.retrieval.types import SearchResult

ROLES = {
    "cheap": {"provider": "siliconflow", "model": "cheap", "model_id": "cheap-id", "capability": "chat"},
    "rerank": {"provider": "siliconflow", "model": "bge", "model_id": "bge-id", "capability": "rerank"},
}
RID = "s84a-stage-test/frost-A/bridge-extract"


def test_leased_repository_delegates_current_chunk_reads(monkeypatch):
    from contextlib import asynccontextmanager
    import backend.weknora_port.runtime as runtime_module

    @asynccontextmanager
    async def lease():
        yield object()

    class CurrentChunkRepository:
        def __init__(self, guard, resolve_tenant, **kwargs):
            self.guard = guard

        async def get_current_chunks(self, identifiers, call_context):
            assert identifiers == ("chunk-1",)
            assert call_context.scope.knowledge_base_ids == ("kb",)
            return {"chunk-1": "current-source"}

    application = SimpleNamespace(
        principal=SimpleNamespace(tenant_id=1, subject_id="owner"),
        leases=SimpleNamespace(lease=lease),
    )
    registry = SimpleNamespace(physical_key="test-physical-key")
    repository = runtime_module.LeasedKnowledgeRepository(application, registry)
    monkeypatch.setattr(runtime_module, "PostgresKnowledgeRepository", CurrentChunkRepository)

    assert asyncio.run(repository.get_current_chunks(("chunk-1",), context())) == {
        "chunk-1": "current-source"
    }

def test_source_identity_skips_only_unsaved_optional_link_fields():
    frozen = {"id": "chunk-1", "content": "source", "knowledge_id": "doc",
              "knowledge_base_id": "kb", "chunk_type": "text", "chunk_index": 1,
              "start_at": 0, "end_at": 6, "metadata": {"source_version": "v1"},
              "chunk_metadata": {"source_version": "v1"}}
    actual = SearchResult("chunk-1", "source", 0, knowledge_id="doc",
                          knowledge_base_id="kb", chunk_type="text", chunk_index=1,
                          start_at=0, end_at=6, pre_chunk_id="previous")
    actual.metadata = {"source_version": "v1"}
    actual.chunk_metadata = {"source_version": "v1"}
    assert runner.verify_source_pair(frozen, actual)["sha256"] == runner.sha("source")
    frozen_with_link = {**frozen, "pre_chunk_id": "different"}
    with pytest.raises(ValueError, match="S84A_CURRENT_SOURCE_BODY_OR_IDENTITY_CHANGED"):
        runner.verify_source_pair(frozen_with_link, actual)
    with pytest.raises(KeyError):
        runner.source_identity({"content": "missing identity"})

def owner(raw=b"frozen"):
    return {"approval_source": "Owner explicit current messages", "stage_id": "stage-test-20261020",
            "max_calls": 8, "max_tokens": 30000, "requests_sha256": runner.sha(raw)}


def case(cid, query="Where is the seminar Ada directed located?"):
    row = asdict(SearchResult("one", "Ada served as director of Example Seminar.", .8,
        knowledge_id="doc", knowledge_base_id="kb", end_at=41,
        metadata={"source_version": "version"}, chunk_metadata={"source_version": "version"}))
    return {"case_id": cid, "arm": "A", "query": query, "rewrite_query": query,
            "knowledge_base_ids": ["kb"], "document_ids": [], "search_results": [row],
            "rerank_results": [row], "merge_results": [row], "evidence_trace": [],
            "rerank_threshold": .3}


def chat_body():
    return {"messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": json.dumps({"question": "Where is it?",
                            "windows": [{"source_chunk_id": "one", "start": 0, "end": 3, "text": "raw", "role": "main"}]})}],
            "temperature": 0, "max_tokens": 256, "enable_thinking": False, "stream": False}


def cfg(role="cheap"):
    return SimpleNamespace(**ROLES[role])


def context(rid=RID):
    return CallContext(rid, TrustedScope("owner", ("kb",)))


@pytest.mark.parametrize("field,value", [
    ("max_calls", 9), ("max_tokens", 30001), ("max_calls", True),
    ("approval_source", "historical grant"), ("requests_sha256", "0" * 64), ("stage_id", "../old"),
])
def test_grant_requires_current_exact_bounded_payload(field, value):
    value_owner = owner()
    value_owner[field] = value
    with pytest.raises(ValueError):
        runner.validate_owner(value_owner, b"frozen")


def test_current_grant_and_payload_are_frozen_without_gold():
    assert runner.validate_owner(owner(), b"frozen") == "s84a-stage-test-20261020/"
    payload = {"schema": runner.SCHEMA, "cases": [case("a"), case("b"), case("negative", "Who invented it?")]}
    assert len(runner.validate_payload(payload)) == 3
    payload["cases"][0]["gold"] = "place"
    with pytest.raises(ValueError, match="GOLD_IN_INPUT"):
        runner.validate_payload(payload)


def test_storage_identity_check_requires_preexisting_marker_without_creating_it(tmp_path):
    settings = SimpleNamespace(storage=tmp_path)
    with pytest.raises(ValueError, match="EXISTING_STORAGE_MARKER_REQUIRED"):
        runner.storage_marker_identity(settings)
    assert list(tmp_path.iterdir()) == []
    marker = tmp_path / ".weknora-python-storage.json"
    marker.write_text(json.dumps({"database": runner.DATABASE, "system_id": runner.SYSTEM_ID}))
    result = runner.storage_marker_identity(settings)
    assert result["database"] == runner.DATABASE
    assert result["system_id"] == runner.SYSTEM_ID


def test_saved_frozen_inputs_only_plan_two_location_cases():
    path = Path(__file__).parent / "fixtures/s8_4_bridge/inputs.json"
    data = runner.read(path)
    cases = runner.validate_payload(data)
    plan = runner.request_plan(cases, "s84a-unittest/")
    assert len(plan) == 4
    assert set(plan.values()) == {"chat", "rerank"}
    assert all("/bridge-" in request for request in plan)
    assert not any("leyden" in request.lower() or "postcode" in request.lower() for request in plan)


def test_plan_does_not_authorize_more_than_eight_possible_calls():
    with pytest.raises(ValueError, match="PLANNED_CALL_BOUND"):
        runner.request_plan([case(str(i)) for i in range(5)], "s84a-unittest/")


def source():
    text = "甲😀. Ada served as director."
    return asdict(SearchResult("one", text, .8, knowledge_id="doc", knowledge_base_id="kb",
        start_at=10, end_at=10 + len(text), metadata={"source_version": "v1"},
        chunk_metadata={"source_version": "v1"}))


@pytest.mark.parametrize("change", ["version", "content", "locator", "scope", "disabled", "derived"])
def test_current_source_guard_rejects_changed_or_non_original_source(change):
    frozen = source()
    actual = deepcopy(frozen)
    if change == "version":
        actual["chunk_metadata"]["source_version"] = "old"
    elif change == "content":
        actual["content"] += " invented"
    elif change == "locator":
        actual["end_at"] = actual["start_at"] + len(actual["content"].encode("utf-8"))
    elif change == "scope":
        actual["knowledge_base_id"] = "other"
    elif change == "disabled":
        actual["is_enabled"] = False
    else:
        actual["content_rewritten"] = True
    with pytest.raises(ValueError):
        runner.verify_source_pair(frozen, actual)


def test_unicode_source_guard_keeps_original_offsets_and_hash():
    value = source()
    proof = runner.verify_source_pair(value, value)
    assert proof["end_at"] - proof["start_at"] == len(value["content"])
    assert proof["sha256"] == runner.sha(value["content"])


@pytest.mark.parametrize("request_id,capability", [
    (RID + "/image-fallback", "chat"), (RID + "/retry", "chat"),
    ("s84a-stage-test/frost-A/final", "chat"), (RID, "embedding"),
])
def test_fence_rejects_unapproved_suffix_or_capability(request_id, capability):
    fence = runner.RequestFence({RID: "chat"}, ROLES)
    with pytest.raises(ValueError, match="EXACT_OPERATION_DENIED"):
        fence.validate(cfg(), chat_body(), context(request_id), capability)
    assert not fence.seen


@pytest.mark.parametrize("field,value", [
    ("max_tokens", 257), ("temperature", .7), ("enable_thinking", True), ("stream", True),
])
def test_fence_freezes_extraction_options(field, value):
    fence = runner.RequestFence({RID: "chat"}, ROLES)
    body = chat_body()
    body[field] = value
    with pytest.raises(ValueError, match="OPTIONS_CHANGED"):
        fence.validate(cfg(), body, context(), "chat")


def test_fence_keeps_formal_gate_and_does_not_retry_after_audit_unknown():
    async def run():
        calls = []
        original_gate = object()

        class Transport:
            gate = original_gate

            @asynccontextmanager
            async def open(self, *args, **kwargs):
                calls.append("send")
                yield object()
                calls.append("settled")

        async def audit():
            calls.append("audit")
            raise ValueError("S84A_NEW_UNKNOWN_STOP")

        model = SimpleNamespace(transport=Transport())
        fence = runner.RequestFence({RID: "chat"}, ROLES, audit=audit)
        fence.install(model)
        assert model.transport.gate is original_gate
        with pytest.raises(ValueError, match="NEW_UNKNOWN_STOP"):
            async with model.transport.open(cfg(), "/chat/completions", chat_body(), context(), capability="chat"):
                pass
        assert calls == ["send", "settled", "audit"]
        with pytest.raises(PermissionError, match="EXACT_OPERATION_DENIED"):
            async with model.transport.open(cfg(), "/chat/completions", chat_body(), context(), capability="chat"):
                pass
        assert calls == ["send", "settled", "audit"]

    asyncio.run(run())


def settlement(capability="chat"):
    role = ROLES["cheap" if capability == "chat" else "rerank"]
    usage = {"status": "REPORTED", "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    row = {"request_id": RID, "capability": capability, **role,
           "outcome": "RESPONSE_RECEIVED", "wire_sha256": "a" * 64, "usage": usage, "finished_at": "now"}
    entry = {**deepcopy(row), "status": "SETTLED", "held_tokens": 15}
    return row, entry


@pytest.mark.parametrize("change", ["unknown", "usage", "wire", "model", "missing_pg"])
def test_dual_ledger_audit_stops_unknown_or_mismatch(change):
    row, entry = settlement()
    rows = [row]
    if change == "unknown":
        entry["status"] = "UNKNOWN"
    elif change == "usage":
        entry["usage"]["total_tokens"] = 99
    elif change == "wire":
        row["wire_sha256"] = "b" * 64
    elif change == "model":
        row["model_id"] = "other-id"
    else:
        rows = []
    with pytest.raises(ValueError):
        runner.validate_settlements(rows, [entry], {RID: "chat"}, ROLES)


def test_dual_ledger_usage_is_reported_by_capability():
    row, entry = settlement()
    totals = runner.validate_settlements([row], [entry], {RID: "chat"}, ROLES)
    assert totals["chat"] == {"calls": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert totals["rerank"]["calls"] == 0


def test_unknown_close_keeps_held_tokens_and_never_reopens_history():
    data = {"status": "STOP", "entries": [{"status": "UNKNOWN", "held_tokens": 1234}]}
    before = deepcopy(data)
    runner.close_execution_data(data, success=False)
    assert data == before
    with pytest.raises(ValueError, match="CANNOT_CLOSE_UNSETTLED"):
        runner.close_execution_data(data, success=True)
    known = {"status": "OPEN", "entries": [{"status": "SETTLED", "held_tokens": 15}]}
    runner.close_execution_data(known, success=True)
    assert known["status"] == "CLOSED" and known["entries"][0]["held_tokens"] == 15


def test_exclusive_artifact_write_refuses_to_replace_prior_stage(tmp_path):
    path = tmp_path / "run.claim"
    runner.write_new(path, {"stage": "first"})
    with pytest.raises(FileExistsError):
        runner.write_new(path, {"stage": "second"})
    assert runner.read(path) == {"stage": "first"}


@pytest.mark.parametrize("change", ["question", "window", "scope", "role"])
def test_bound_case_rejects_changed_question_source_span_or_scope(change):
    value = case("frost-A")
    source_row = value["search_results"][0]
    ctx = context("s84a-stage-test/frost-A")
    fence = runner.RequestFence({RID: "chat"}, ROLES)
    fence.bind_case(value, ctx, SimpleNamespace(bridge_trace=[]))
    body = chat_body()
    data = {"question": value["query"], "windows": [{
        "source_chunk_id": source_row["id"], "start": 0, "end": len(source_row["content"]),
        "text": source_row["content"], "role": "main"}]}
    if change == "question":
        data["question"] = "Other question"
    elif change == "window":
        data["windows"][0]["text"] = "Invented evidence"
    elif change == "role":
        data["windows"][0]["role"] = "preceding"
    wire_context = ctx.child("bridge-extract")
    if change == "scope":
        wire_context = CallContext(RID, TrustedScope("owner", ("other",)))
    body["messages"][1]["content"] = json.dumps(data)
    with pytest.raises(ValueError):
        fence.validate(cfg(), body, wire_context, "chat")
    assert not fence.seen


def test_bound_case_rerank_requires_program_q1_and_exact_original_passages():
    value = case("frost-A")
    rid = "s84a-stage-test/frost-A/bridge-rerank"
    ctx = context("s84a-stage-test/frost-A")
    state = SimpleNamespace(bridge_trace=[{"decision": "SECOND_RERANK_INPUT", "q1": "Where is Example Seminar located?",
                                         "candidate_ids": ["one"]}])
    fence = runner.RequestFence({rid: "rerank"}, ROLES)
    fence.bind_case(value, ctx, state)
    with pytest.raises(ValueError, match="PROGRAM_DERIVED_RERANK_CHANGED"):
        fence.validate(cfg("rerank"), {"query": state.bridge_trace[0]["q1"], "documents": ["Invented source"]},
                       ctx.child("bridge-rerank"), "rerank")
    fence.validate(cfg("rerank"), {"query": state.bridge_trace[0]["q1"],
                                  "documents": [value["search_results"][0]["content"]]},
                   ctx.child("bridge-rerank"), "rerank")


def test_fence_preserves_typed_cancellation_while_audit_detects_unknown():
    from backend.weknora_port.models.types import CallCancelled

    async def run():
        class Transport:
            @asynccontextmanager
            async def open(self, *args, **kwargs):
                raise CallCancelled(sent=True)
                yield

        async def audit():
            raise ValueError("S84A_NEW_UNKNOWN_STOP")

        model = SimpleNamespace(transport=Transport())
        runner.RequestFence({RID: "chat"}, ROLES, audit=audit).install(model)
        with pytest.raises(CallCancelled):
            async with model.transport.open(cfg(), "/chat/completions", chat_body(), context(), capability="chat"):
                pass

    asyncio.run(run())


def test_fence_accepts_formal_provider_adapter_wire_without_real_network():
    from dataclasses import replace
    import httpx
    from backend.weknora_port.models import AsyncHTTPTransport, ProviderModel, provider_config, ChatOptions, Message
    from backend.weknora_port.models.transport import resolve_endpoint

    async def run():
        calls = []

        class Gate:
            async def authorize(self, call):
                calls.append(call)
            async def finish(self, call, *, outcome, usage):
                return True

        config = replace(provider_config("siliconflow", "Qwen/Qwen3.5-4B"), model_id="cheap-id")
        roles = deepcopy(ROLES)
        roles["cheap"].update(provider=config.provider, model=config.model, model_id=config.model_id)
        transport = AsyncHTTPTransport(allowed_endpoints=[resolve_endpoint(config, "/chat/completions")],
            gate=Gate(), credential=lambda provider: "SIMULATED_NOT_A_KEY",
            http_transport=httpx.MockTransport(lambda req: httpx.Response(200, json={
                "model": config.model, "choices": [{"message": {"content": "{\"status\":\"NO_FIRST_HOP\"}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})))
        model = runner.RequestFence({RID: "chat"}, roles).install(ProviderModel(config, transport))
        try:
            response = await model.chat((Message("system", SYSTEM), Message("user", chat_body()["messages"][1]["content"])),
                ChatOptions(temperature=0, thinking=False, max_tokens=256, format="json",
                            send_temperature=True), context())
            assert response.finish_reason == "stop" and response.usage.total_tokens == 15
            assert len(calls) == 1 and calls[0].context.request_id == RID
        finally:
            await transport.aclose()

    asyncio.run(run())

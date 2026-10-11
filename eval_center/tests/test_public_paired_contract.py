from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from eval_center.paired_state import (
    ARMS,
    PairedContractError,
    begin_attempt,
    finish_attempt,
    initialize_state,
    load_frozen_cohort,
    pending_attempts,
    register_run,
    validate_paired_results,
)
from eval_center.tests.test_public_paired_state import _sha, _temporary_state_path, _remove_temporary_state
from eval_center.public_runner import (
    invoke_paired_generation_arm, validate_route_triplet, _memory_free_container,
)


def _hash(char: str) -> str:
    return char * 64


def _result(qid: str, arm: str, *, context: str = "b") -> dict:
    return {
        "qid": qid,
        "arm": arm,
        "dataset": "longbench-zh",
        "dataset_version": "public-v1",
        "corpus_sha256": _hash("a"),
        "pool_sha256": _hash("b"),
        "qrels_sha256": _hash("c"),
        "gold_sha256": _hash("d"),
        "index_sha256": _hash("e"),
        "prompt_sha256": _hash("f"),
        "context_sha256": _hash(context),
    }


def test_frozen_dev24_cohort_is_readable_and_balanced():
    path = Path(__file__).resolve().parents[2] / "var/reports/v6-first-evaluation-20261010/cohort_dev24.jsonl"
    if not path.is_file():
        pytest.skip("v6 dev24 artifact is not present in this checkout")
    rows = load_frozen_cohort(path)
    assert len(rows) == 24
    assert {dataset: sum(row["dataset"] == dataset for row in rows)
            for dataset in ("scifact", "miracl-zh", "longbench-zh")} == {
                "scifact": 8, "miracl-zh": 8, "longbench-zh": 8,
            }


def test_paired_results_require_same_inputs_and_all_three_arms():
    rows = [_result("qid-1", arm) for arm in ARMS]
    assert validate_paired_results(rows) == {
        "status": "PAIRED_INPUTS_VERIFIED", "qid_count": 1,
        "arm_count": 3, "result_count": 3,
    }
    with pytest.raises(PairedContractError, match="paired_input_mismatch"):
        validate_paired_results([_result("qid-1", arm, context="9" if arm == "DYNAMIC" else "b")
                                 for arm in ARMS])
    with pytest.raises(PairedContractError, match="paired_arm_missing"):
        validate_paired_results([_result("qid-1", arm) for arm in ARMS[:2]])


def test_checkpoint_prevents_duplicate_attempts_and_keeps_pending_rows(tmp_path):
    state = tmp_path / "paired.sqlite3"
    initialize_state(state)
    register_run(state, run_id="run-1", cohort_sha256=_hash("a"),
                 source_manifest_sha256=_hash("b"), code_sha="1" * 40,
                 deadline_utc="2026-10-10T04:25:44Z", now_utc="2026-10-09T20:25:44Z")
    begin_attempt(state, run_id="run-1", qid="qid-1", arm="CHEAP_ONLY",
                  input_identity_sha256=_hash("c"), now_utc="2026-10-09T20:30:00Z")
    assert len(pending_attempts(state, "run-1")) == 1
    with pytest.raises(PairedContractError, match="attempt_already_exists_no_retry"):
        begin_attempt(state, run_id="run-1", qid="qid-1", arm="CHEAP_ONLY",
                      input_identity_sha256=_hash("c"), now_utc="2026-10-09T20:31:00Z")
    finish_attempt(state, run_id="run-1", qid="qid-1", arm="CHEAP_ONLY",
                   status="unknown", error_code="usage_unknown", now_utc="2026-10-09T20:32:00Z")
    assert pending_attempts(state, "run-1") == []
    with pytest.raises(PairedContractError, match="attempt_already_exists_no_retry"):
        begin_attempt(state, run_id="run-1", qid="qid-1", arm="CHEAP_ONLY",
                      input_identity_sha256=_hash("c"), now_utc="2026-10-09T20:33:00Z")


def test_deadline_blocks_attempt_before_dispatch(tmp_path):
    state = tmp_path / "paired.sqlite3"
    register_run(state, run_id="run-2", cohort_sha256=_hash("a"),
                 source_manifest_sha256=_hash("b"), code_sha="2" * 40,
                 deadline_utc="2026-10-10T04:25:44Z", now_utc="2026-10-09T20:25:44Z")
    with pytest.raises(PairedContractError, match="run_deadline_expired"):
        begin_attempt(state, run_id="run-2", qid="qid-1", arm="DYNAMIC",
                      input_identity_sha256=_hash("c"), now_utc="2026-10-10T04:25:44Z")


def test_route_adapter_requires_guarded_factory_bindings_and_uses_quick_chain():
    calls = []
    state, state_root = _temporary_state_path()
    from eval_center.paired_state import register_run
    register_run(state, run_id="run", cohort_sha256=_sha("a"),
                 source_manifest_sha256=_sha("b"), code_sha="1" * 40,
                 deadline_utc="2026-10-10T04:25:44Z", now_utc="2026-10-09T20:25:44Z")
    class Binding:
        def __init__(self, cheap=False):
            self.usage_guard = object()
            self.gateway = SimpleNamespace(usage_guard=object()) if cheap else None
            self.invoke = lambda envelope, **kwargs: "fake answer"

    class QuickChain:
        def __init__(self):
            self.context_manager = SimpleNamespace(memory_retriever=object())
            self.generation_roles = {"chat_cheap": Binding(True), "chat_expensive": Binding()}
        def clone_without_memory(self):
            return QuickChain()
        def invoke(self, *args, **kwargs):
            from backend.app.application.run_metrics import current_metrics
            calls.append((args, kwargs))
            mode = kwargs["settings"].router_policy.mode
            current_metrics().rule_router = {
                "mode": mode, "context_hash": _hash("a"), "prompt_hash": _hash("b"),
                "envelope_identity": _hash("c"), "attempts": [{"send_status": "SIMULATED"}],
                "simulation": "SIMULATED",
            }
            return SimpleNamespace(answer="fake answer", error_code=None, citations=(), evidence=())

    quick_chain = QuickChain()
    container = SimpleNamespace(quick_chain=quick_chain, store=object())
    guard_calls = []
    try:
        output = invoke_paired_generation_arm(
            container, "public question", object(), arm="DYNAMIC", qid="qid-1", run_id="run",
            state_run_id="run",
            conversation_id="conversation", cloud_allowed_by_kb={"kb": True},
            dispatch_guard=lambda: guard_calls.append("checked"), state_path=state,
            input_identity_sha256=_sha("c"),
        )
        assert output["result"].answer == "fake answer"
        assert guard_calls == ["checked", "checked"]
        assert len(calls) == 1
        assert calls[0][1]["settings"].router_policy.mode == "DYNAMIC"
        assert calls[0][1]["run_id"] == "run"
        resumed = invoke_paired_generation_arm(
            container, "public question", object(), arm="DYNAMIC", qid="qid-1", run_id="run",
            state_run_id="run", conversation_id="conversation", cloud_allowed_by_kb={"kb": True},
            dispatch_guard=lambda: guard_calls.append("resume-check"), state_path=state,
            input_identity_sha256=_sha("c"),
        )
        assert resumed["attempt_status"] == "simulated"
        assert len(calls) == 1, "resume must reuse the checkpoint without provider dispatch"
    finally:
        _remove_temporary_state(state, state_root)


def test_route_adapter_fails_closed_without_dispatch_guard():
    quick_chain = SimpleNamespace(generation_roles={"chat_cheap": object(), "chat_expensive": object()})
    with pytest.raises(Exception, match="paired_dispatch_guard_required"):
        invoke_paired_generation_arm(
            SimpleNamespace(quick_chain=quick_chain), "question", object(),
            arm="CHEAP_ONLY", qid="qid-1", run_id="run", state_run_id="run",
            conversation_id="conversation",
            cloud_allowed_by_kb={"kb": True}, dispatch_guard=None,
            state_path=Path("unused"), input_identity_sha256=_sha("c"),
        )


def test_pair_memory_is_isolated_and_provider_envelope_is_checked():
    original_memory = object()
    original_invoke = lambda envelope, **kwargs: "sent"
    binding = SimpleNamespace(invoke=original_invoke)
    context_manager = SimpleNamespace(memory_retriever=original_memory)
    original_chain = SimpleNamespace(context_manager=context_manager, generation_roles={"chat_cheap": binding})
    cloned_binding = SimpleNamespace(invoke=original_invoke)
    cloned_chain = SimpleNamespace(context_manager=SimpleNamespace(memory_retriever=original_memory),
                                   generation_roles={"chat_cheap": cloned_binding})
    original_chain.clone_without_memory = lambda: cloned_chain
    container = SimpleNamespace(quick_chain=original_chain, store=object())
    isolated = _memory_free_container(container)
    isolated_binding = isolated.quick_chain.generation_roles["chat_cheap"]
    assert isolated.quick_chain is cloned_chain
    assert cloned_chain.context_manager.memory_retriever is None
    assert context_manager.memory_retriever is original_memory
    assert binding.invoke is original_invoke
    assert isolated_binding.invoke(SimpleNamespace(prompt="public prompt"),
                                   request_id="r", run_id="r", scope=object()) == "sent"
    with pytest.raises(Exception, match="PAIRED_MEMORY_CONTEXT_FORBIDDEN"):
        isolated_binding.invoke(SimpleNamespace(prompt="UNTRUSTED LONG-TERM MEMORY: injected"),
                                request_id="r", run_id="r", scope=object())


def test_route_triplet_requires_same_context_and_prompt_hashes():
    outputs = {
        arm: {"route": {"mode": arm, "context_hash": _hash("a"),
                        "prompt_hash": _hash("b"), "envelope_identity": _hash("c")}}
        for arm in ARMS
    }
    assert validate_route_triplet(outputs) == {
        "context_hash": _hash("a"), "prompt_hash": _hash("b"), "envelope_identity": _hash("c")
    }
    outputs["EXPENSIVE_ONLY"]["route"]["context_hash"] = _hash("9")
    with pytest.raises(Exception, match="paired_prompt_or_context_mismatch"):
        validate_route_triplet(outputs)

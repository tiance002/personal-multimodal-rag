"""SIMULATED dependency tests of the shared production cohort execution path."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.application.quick_chain import LangChainQuickChain
from backend.app.application.rule_router_v1 import GenerationRole
from backend.app.ports.providers import ProviderRequestNotSent
from eval_center.paired_simulation import build_simulated_runtime
from eval_center.paired_state import (
    ARMS, PairedContractError, attempts_for_qid, begin_attempt, checkpoint_envelope,
    finish_attempt, load_case_results, register_run, sha256_file,
    claim_provider_send, provider_send_claim_record, finish_provider_send, close_run,
    initialize_state, run_record,
    SQLiteProviderSendClaims,
    CurrentProviderSendClaims, provider_send_claim_scope,
)
from eval_center.public_benchmark_cli import _parser, _run
from eval_center.public_data import PublicCase
from eval_center.public_runner import (
    _paired_runtime_admission, _readback_citations, invoke_paired_generation_arm,
    retry_public_paired_telemetry, run_public_paired_generation_case,
    _memory_free_container,
)


def _args(state, phase="paired-run", run_id="runtime-injected-simulation"):
    return _parser().parse_args(["--phase", phase, "--run-id", run_id, "--state-path", str(state)])


def _register(state, run_id="case", qids=None):
    register_run(state, run_id=run_id, cohort_sha256="a" * 64,
        source_manifest_sha256="b" * 64, code_sha="c" * 64,
        deadline_utc="2026-10-10T04:25:44Z", expected_qids=qids or ["q"])


def _case(runtime, state, *, work_guard=None, answers=("GOLD_SECRET_NEVER_IN_GENERATION",)):
    return run_public_paired_generation_case(
        runtime.containers["longbench-zh"],
        PublicCase("q", "What does the public evidence say?", {"GOLD_DOC_NEVER_IN_GENERATION": 1},
                   "development", answers), runtime.scopes["longbench-zh"],
        run_id="case", dataset_name="longbench-zh", dataset_version="SIMULATED-case",
        state_path=state, shared_identity={key: "d" * 64 for key in (
            "corpus_sha256", "pool_sha256", "qrels_sha256", "gold_sha256", "index_sha256")},
        cloud_allowed_by_kb=runtime.cloud_allowed_by_kb,
        dispatch_guard=runtime.dispatch_guard, work_guard=work_guard,
        execution_kind="SIMULATED")


@pytest.fixture(scope="module")
def completed_injected_runtime(tmp_path_factory):
    state = tmp_path_factory.mktemp("shared-runtime") / "center.sqlite3"
    runtime = build_simulated_runtime(state_path=state, run_id="runtime-injected-simulation")
    assert type(runtime.containers["scifact"].quick_chain) is LangChainQuickChain
    # Use the paired-run CLI phase itself with controlled dependencies. This
    # reaches its positive cohort path while preserving every SIMULATED label.
    first = _run(_args(state), runtime=runtime)
    return state, runtime, first


def test_paired_run_positive_injection_uses_complete_common_scheduler(completed_injected_runtime):
    state, runtime, report = completed_injected_runtime
    assert report["scheduler"] == "run_public_paired_cohort"
    assert report["status"] == "SIMULATED_PAIRED_COHORT_COMPLETED"
    assert report["qid_count"] == 24
    assert report["quickchain_invocations"] == report["new_quickchain_invocations"] == 72
    assert report["attempt_counts"] == dict.fromkeys(ARMS, 24)
    assert report["provider_calls"] == report["database_queries"] == 0
    assert report["current_provider_observations"] == {
        "execution_kind": "SIMULATED", "real_provider_calls": 0,
        "cheap_fixture_calls": 48, "deepseek_fixture_calls": 24}
    with sqlite3.connect(state) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"experiments", "experiment_cases", "paired_runs", "paired_attempts"} <= tables


def test_same_id_across_both_phases_reuses_without_dispatch(completed_injected_runtime):
    state, runtime, first = completed_injected_runtime
    observed = len(runtime.containers["scifact"].simulated_provider_calls)
    second = _run(_args(state, "paired-simulate"), runtime=runtime)
    assert second["results_sha256"] == first["results_sha256"]
    assert second["reused_qids"] == 24
    assert second["new_quickchain_invocations"] == 0
    assert len(runtime.containers["scifact"].simulated_provider_calls) == observed


def test_gold_and_retrieval_denominators_exclude_unavailable_dimensions(completed_injected_runtime):
    state, _, report = completed_injected_runtime
    assert report["answer_gold_denominator"] == 8
    assert report["answer_not_scored_no_gold_rows"] == 16
    assert report["retrieval_qrels_available_rows"] == 24
    assert report["retrieval_scored_rows"] == 0
    assert report["retrieval_macro"] == report["context_macro"] == {"metrics": {}, "counts": {}}
    for arm in ARMS:
        assert report["answer_macro"][arm]["counts"]["character_f1"] == {"evaluated": 8, "unavailable": 16}
    rows = load_case_results(state, report["run_id"])
    assert all(row["observed_retrieval_calls"] == 1 for row in rows)
    assert all(cell["citation_readback_status"] == "NOT_RUN_SIMULATED"
               for row in rows for cell in row["arms"].values())


def test_provider_checkpoint_precedes_send_and_gold_never_enters_prompt(tmp_path, monkeypatch):
    state = tmp_path / "case.sqlite3"
    runtime = build_simulated_runtime(state_path=state, run_id="gold-boundary")
    _register(state)
    original = GenerationRole.invoke
    observed = []
    def invoke(role, envelope, **kwargs):
        with sqlite3.connect(state) as db:
            pending = db.execute("SELECT result_json FROM paired_attempts WHERE status='pending'").fetchall()
        assert len(pending) == 1
        checkpoint = json.loads(pending[0][0])["dispatch_identity"]
        assert checkpoint["envelope_identity"] == envelope.identity
        send_claim = provider_send_claim_record(state, request_id=kwargs["request_id"])
        assert send_claim["status"] == "pending" and send_claim["send_claimed_at_utc"]
        assert send_claim["purpose"] == "answer"
        assert "GOLD_SECRET_NEVER_IN_GENERATION" not in envelope.prompt
        assert "GOLD_DOC_NEVER_IN_GENERATION" not in envelope.prompt
        observed.append((envelope.prompt, envelope.context_hash, envelope.identity))
        return original(role, envelope, **kwargs)
    monkeypatch.setattr(GenerationRole, "invoke", invoke)
    result = _case(runtime, state)
    assert result["status"] == "PAIRED_INPUTS_VERIFIED"
    assert len(observed) == 3 and len(set(observed)) == 1
    assert all(cell["route"]["threshold"] == "0.35" for cell in result["arms"].values())
    assert all(len(cell["route"]["attempts"]) == 1 for cell in result["arms"].values())


def test_actual_context_change_is_rejected_before_later_send(tmp_path):
    state = tmp_path / "identity.sqlite3"
    _register(state)
    first = dict(prompt_hash="d" * 64, context_hash="e" * 64, envelope_identity="f" * 64)
    begin_attempt(state, run_id="case", qid="q", arm="CHEAP_ONLY", input_identity_sha256="a" * 64)
    checkpoint_envelope(state, run_id="case", qid="q", arm="CHEAP_ONLY", identity=first)
    finish_attempt(state, run_id="case", qid="q", arm="CHEAP_ONLY", status="simulated", result={"answer": "SIMULATED"})
    begin_attempt(state, run_id="case", qid="q", arm="DYNAMIC", input_identity_sha256="a" * 64)
    with pytest.raises(PairedContractError, match="mismatch_before_send"):
        checkpoint_envelope(state, run_id="case", qid="q", arm="DYNAMIC",
                            identity={**first, "context_hash": "9" * 64})
    assert attempts_for_qid(state, run_id="case", qid="q")["DYNAMIC"]["status"] == "pending"


def test_one_failed_cell_is_retained_and_not_scored_as_zero(tmp_path, monkeypatch):
    state = tmp_path / "failure.sqlite3"
    runtime = build_simulated_runtime(state_path=state, run_id="failed-cell")
    _register(state)
    gateway = runtime.containers["scifact"].quick_chain.generation_roles["chat_cheap"].gateway
    original = gateway.generate
    calls = []
    def generate(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ProviderRequestNotSent("SIMULATED_FIXTURE_DENIAL")
        return original(*args, **kwargs)
    monkeypatch.setattr(gateway, "generate", generate)
    result = _case(runtime, state)
    assert set(result["arms"]) == set(ARMS)
    failed = result["arms"]["CHEAP_ONLY"]
    assert failed["attempt_status"] in {"blocked", "failed"}
    assert failed["answer_metrics"] == "NOT_SCORED"
    assert failed["answer_scoring_status"] == "NOT_SCORED_ATTEMPT_FAILED"
    assert len(attempts_for_qid(state, run_id="case", qid="q")) == 3


def test_unknown_sent_attempt_cannot_be_replayed(tmp_path):
    state = tmp_path / "unknown.sqlite3"
    runtime = build_simulated_runtime(state_path=state, run_id="unknown")
    _register(state)
    begin_attempt(state, run_id="case", qid="q", arm="DYNAMIC", input_identity_sha256="a" * 64)
    finish_attempt(state, run_id="case", qid="q", arm="DYNAMIC", status="unknown",
                   result={"answer": "", "route": None})
    with pytest.raises(Exception, match="no_retry"):
        invoke_paired_generation_arm(runtime.containers["scifact"], "question", runtime.scopes["scifact"],
            arm="DYNAMIC", qid="q", run_id="arm", state_run_id="case", conversation_id="arm",
            cloud_allowed_by_kb=runtime.cloud_allowed_by_kb, dispatch_guard=lambda: None,
            state_path=state, input_identity_sha256="a" * 64)
    assert runtime.containers["scifact"].simulated_provider_calls == []


def test_retagging_simulated_runtime_real_does_not_open_any_guard(tmp_path):
    state = tmp_path / "retag.sqlite3"
    runtime = build_simulated_runtime(state_path=state, run_id="retag-rejected")
    manifest = json.loads(Path("var/reports/v6-first-evaluation-20261010/source_manifest.json").read_text(encoding="utf-8"))
    missing, _ = _paired_runtime_admission(replace(runtime, execution_kind="REAL"),
                                          manifest=manifest, index_manifest_path=None)
    assert "frozen_index_manifest" in missing
    assert "real_typed_grant:chat_expensive" in missing
    # A separately authorized backend may now implement policy admission.
    # REAL still requires real bindings/capacity, regardless of that interface.
    assert "real_typed_grant:chat_cheap" in missing
    assert "production_capacity_probe" in missing
    assert runtime.containers["scifact"].simulated_provider_calls == []


def test_missing_runtime_does_not_construct_application_or_read_settings(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("missing runtime must not construct application or read keys/settings")
    monkeypatch.setattr("backend.app.bootstrap.build_container", forbidden)
    monkeypatch.setattr("backend.app.config.Settings.from_env", forbidden)
    report = _run(_args(tmp_path / "never-created.sqlite3"))
    assert report["status"] == "BLOCKED_ZERO_SEND"
    assert report["quickchain_invocations"] == report["provider_calls"] == report["database_queries"] == 0
    assert not (tmp_path / "never-created.sqlite3").exists()


def test_round_deadline_checked_before_citation_readback():
    calls = []
    def expired():
        raise RuntimeError("round deadline")
    with pytest.raises(RuntimeError, match="deadline"):
        _readback_citations(SimpleNamespace(evidence=(), citations=("E1",)),
            SimpleNamespace(get_chunk=lambda chunk_id: calls.append(chunk_id)), object(), work_guard=expired)
    assert calls == []


def test_expired_round_recovery_does_not_aggregate_or_resend(completed_injected_runtime, monkeypatch):
    state, runtime, report = completed_injected_runtime
    from datetime import datetime, timezone
    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 10, 5, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("eval_center.public_runner.datetime", ExpiredClock)
    def forbidden(*args):
        pytest.fail("aggregation must stop at the round/absolute deadline")
    monkeypatch.setattr("eval_center.public_runner.aggregate_metrics", forbidden)
    before = len(runtime.containers["scifact"].simulated_provider_calls)
    recovered = _run(_args(state), runtime=runtime)
    assert recovered["cohort_scoring_status"] == "NOT_RUN_DEADLINE_REACHED"
    assert recovered["new_quickchain_invocations"] == 0
    assert len(runtime.containers["scifact"].simulated_provider_calls) == before
    assert recovered["results_sha256"] == report["results_sha256"]


def test_expired_telemetry_retry_does_not_write(completed_injected_runtime, monkeypatch):
    state, _, report = completed_injected_runtime
    def forbidden(*args, **kwargs):
        pytest.fail("telemetry must not begin after deadline")
    def expired():
        raise RuntimeError("round deadline")
    monkeypatch.setattr("eval_center.public_langfuse.write_and_readback_public_event", forbidden)
    with pytest.raises(RuntimeError, match="deadline"):
        retry_public_paired_telemetry(state_path=state, run_id=report["run_id"],
                                     client=object(), work_guard=expired)


def test_telemetry_only_retry_preserves_generation_and_original_case_evidence(completed_injected_runtime, monkeypatch):
    state, runtime, report = completed_injected_runtime
    original_hash = report["results_sha256"]
    calls_before = len(runtime.containers["scifact"].simulated_provider_calls)
    sent = []
    def event(client, **kwargs):
        sent.append(kwargs)
        return {"trace_id": "f" * 32}
    monkeypatch.setattr("eval_center.public_langfuse.write_and_readback_public_event", event)
    retry = retry_public_paired_telemetry(state_path=state, run_id=report["run_id"],
                                       client=object(), work_guard=lambda: None)
    assert retry == {"delivered": 72, "unknown": 0, "generation_dispatches": 0}
    assert len(sent) == 72
    assert len(runtime.containers["scifact"].simulated_provider_calls) == calls_before
    from eval_center.public_runner import _canonical_hash
    assert _canonical_hash(load_case_results(state, report["run_id"])) == original_hash
    # Stable receipts mean a second telemetry-only pass does not write again.
    retry_public_paired_telemetry(state_path=state, run_id=report["run_id"], client=object(), work_guard=lambda: None)
    assert len(sent) == 72


def _calibration_run(state, run_id="cal", qid="calibration:q"):
    register_run(state, run_id=run_id, cohort_sha256="a" * 64,
        source_manifest_sha256="b" * 64, code_sha="c" * 64,
        deadline_utc="2026-10-10T04:25:44Z", expected_qids=[qid],
        execution_purpose="CALIBRATION")


def _calibration_claim(state, *, run_id="cal", qid="calibration:q", request_id="sf-calibration-stable-1"):
    return claim_provider_send(state, run_id=run_id, qid=qid, arm="CHEAP_ONLY",
        request_id=request_id, input_identity_sha256="d" * 64,
        request_identity_sha256="e" * 64, purpose="calibration", provider="siliconflow",
        model="XingChenAGI/Xing4.0-29B", role="chat_cheap")


def test_calibration_claim_commits_before_send_in_same_registry_without_scoring(tmp_path):
    state = tmp_path / "same-center.sqlite3"
    _calibration_run(state)
    claim = _calibration_claim(state)
    with sqlite3.connect(state) as db:
        row = db.execute("SELECT request_id,send_claimed_at_utc,purpose,status FROM paired_provider_calls").fetchone()
        assert row == (claim.request_id, claim.claimed_at_utc, "calibration", "pending")
        assert db.execute("SELECT COUNT(*) FROM paired_case_results").fetchone()[0] == 0
    finish_provider_send(state, request_id=claim.request_id, status="unknown",
                         result={"execution_kind": "SIMULATED", "send_outcome": "UNKNOWN"})
    assert provider_send_claim_record(state, request_id=claim.request_id)["status"] == "unknown"
    finish_attempt(state, run_id="cal", qid="calibration:q", arm="CHEAP_ONLY", status="unknown")
    close_run(state, run_id="cal", status="completed")
    assert run_record(state, "cal")["execution_purpose"] == "CALIBRATION"
    assert load_case_results(state, "cal") == []


@pytest.mark.parametrize("status", ["unknown", "succeeded", "failed", "blocked", "simulated"])
def test_durable_send_claim_never_retries_any_recorded_outcome(tmp_path, status):
    state = tmp_path / (status + ".sqlite3")
    _calibration_run(state)
    claim = _calibration_claim(state)
    # A second claim is denied even while the first is pending / after a crash.
    with pytest.raises(PairedContractError, match="no_retry"):
        _calibration_claim(state)
    finish_provider_send(state, request_id=claim.request_id, status=status)
    with pytest.raises(PairedContractError, match="no_retry"):
        _calibration_claim(state)
    assert len(attempts_for_qid(state, run_id="cal", qid="calibration:q")) == 1


def test_stable_request_id_is_unique_across_cohort_and_calibration_runs(tmp_path):
    state = tmp_path / "registry.sqlite3"
    _register(state)
    begin_attempt(state, run_id="case", qid="q", arm="CHEAP_ONLY", input_identity_sha256="d" * 64)
    claim_provider_send(state, run_id="case", qid="q", arm="CHEAP_ONLY",
        request_id="sf-calibration-stable-1", input_identity_sha256="d" * 64,
        request_identity_sha256="e" * 64, provider="siliconflow", model="XingChenAGI/Xing4.0-29B", role="chat_cheap")
    _calibration_run(state)
    with pytest.raises(PairedContractError, match="already_claimed_no_retry"):
        _calibration_claim(state)
    # Failed UNIQUE reservation rolls back the newly inserted calibration row.
    assert attempts_for_qid(state, run_id="cal", qid="calibration:q") == {}


def test_calibration_cannot_pollute_cohort_run_or_change_registered_purpose(tmp_path):
    state = tmp_path / "purpose.sqlite3"
    _register(state)
    with pytest.raises(PairedContractError, match="purpose_mismatch"):
        _calibration_claim(state, run_id="case", qid="q")
    assert attempts_for_qid(state, run_id="case", qid="q") == {}
    with pytest.raises(PairedContractError, match="run_identity_conflict"):
        register_run(state, run_id="case", cohort_sha256="a" * 64,
            source_manifest_sha256="b" * 64, code_sha="c" * 64,
            deadline_utc="2026-10-10T04:25:44Z", expected_qids=["q"], execution_purpose="CALIBRATION")


def test_cross_process_race_allows_exactly_one_simulated_send(tmp_path):
    state = tmp_path / "race.sqlite3"
    _calibration_run(state)
    start, sent = tmp_path / "start", tmp_path / "SIMULATED-send-receipt.txt"
    script = '''
import json, pathlib, sys, time
from eval_center.paired_state import claim_provider_send, PairedContractError, provider_send_claim_record
state, start, sent = map(pathlib.Path, sys.argv[1:])
deadline = time.monotonic() + 5
while not start.exists():
    if time.monotonic() >= deadline: raise RuntimeError("SIMULATED race barrier timeout")
    time.sleep(0.01)
try:
    claim = claim_provider_send(state, run_id="cal", qid="calibration:q", arm="CHEAP_ONLY",
        request_id="sf-calibration-stable-1", input_identity_sha256="d"*64,
        request_identity_sha256="e"*64, purpose="calibration", provider="siliconflow",
        model="XingChenAGI/Xing4.0-29B", role="chat_cheap")
    assert provider_send_claim_record(state, request_id=claim.request_id)["send_claimed_at_utc"]
    with sent.open("a", encoding="utf-8") as stream: stream.write("SIMULATED_SEND\\n")
    print(json.dumps({"status":"CLAIMED", "execution_kind":"SIMULATED"}))
except PairedContractError as exc:
    print(json.dumps({"status":"REJECTED", "error":str(exc)}))
'''
    children = []
    try:
        for _ in range(2):
            children.append(subprocess.Popen([sys.executable, "-B", "-X", "utf8", "-c", script,
                str(state), str(start), str(sent)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=Path(__file__).resolve().parents[2]))
        start.write_text("SIMULATED barrier", encoding="utf-8")
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=10)
            assert child.returncode == 0, stderr.decode("utf-8", errors="replace")
            results.append(json.loads(stdout.decode("utf-8")))
        assert sorted(row["status"] for row in results) == ["CLAIMED", "REJECTED"]
        assert sent.read_text(encoding="utf-8").splitlines() == ["SIMULATED_SEND"]
        assert len(attempts_for_qid(state, run_id="cal", qid="calibration:q")) == 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=5)


def test_backend_callback_separates_helpers_and_answer_and_unknown_slot_cannot_be_renamed(tmp_path):
    state = tmp_path / "helpers.sqlite3"
    _register(state)
    begin_attempt(state, run_id="case", qid="q", arm="CHEAP_ONLY", input_identity_sha256="d" * 64)
    callback = SQLiteProviderSendClaims(state, "case", "q", "CHEAP_ONLY", "d" * 64)
    ids = {}
    for purpose in ("understand", "rewrite", "expansion", "answer"):
        ids[purpose] = callback.request_id(purpose=purpose)
        assert ids[purpose] == callback.request_id(purpose=purpose)
        claim = callback(request_id=ids[purpose], purpose=purpose, provider="siliconflow",
            model="XingChenAGI/Xing4.0-29B", role="chat_cheap", request_identity_sha256="e" * 64)
        assert claim.purpose == purpose
        callback.settle(request_id=claim.request_id,
                        status="unknown" if purpose == "understand" else "simulated")
    assert len(set(ids.values())) == 4
    with pytest.raises(PairedContractError, match="already_claimed_no_retry"):
        callback(request_id="new-id-cannot-retry-unknown-helper", purpose="understand",
            provider="siliconflow", model="XingChenAGI/Xing4.0-29B", role="chat_cheap",
            request_identity_sha256="e" * 64)
    receipt = attempts_for_qid(state, run_id="case", qid="q")["CHEAP_ONLY"]
    assert len(receipt["provider_calls"]) == 4
    assert provider_send_claim_record(state, request_id=ids["understand"])["status"] == "unknown"


def test_arm_recovery_does_not_replay_unknown_helper(tmp_path):
    state = tmp_path / "helper-recovery.sqlite3"
    runtime = build_simulated_runtime(state_path=state, run_id="helper-recovery")
    _register(state)
    begin_attempt(state, run_id="case", qid="q", arm="DYNAMIC", input_identity_sha256="d" * 64)
    callback = SQLiteProviderSendClaims(state, "case", "q", "DYNAMIC", "d" * 64)
    request_id = callback.request_id(purpose="understand")
    callback(request_id=request_id, purpose="understand", provider="siliconflow",
        model="XingChenAGI/Xing4.0-29B", role="chat_cheap", request_identity_sha256="e" * 64)
    callback.settle(request_id=request_id, status="unknown")
    with pytest.raises(Exception, match="no_retry"):
        invoke_paired_generation_arm(runtime.containers["scifact"], "question", runtime.scopes["scifact"],
            arm="DYNAMIC", qid="q", run_id="arm", state_run_id="case", conversation_id="arm",
            cloud_allowed_by_kb=runtime.cloud_allowed_by_kb, dispatch_guard=lambda: None,
            state_path=state, input_identity_sha256="d" * 64)
    assert runtime.containers["scifact"].simulated_provider_calls == []
    assert provider_send_claim_record(state, request_id=request_id)["status"] == "unknown"


def test_legacy_rows_keep_original_outcomes_without_fabricated_provider_claims(tmp_path):
    state = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(state) as db:
        db.executescript('''
            CREATE TABLE paired_runs (
                run_id TEXT PRIMARY KEY,cohort_sha256 TEXT,source_manifest_sha256 TEXT,code_sha TEXT,
                deadline_utc TEXT,dispatch_deadline_utc TEXT,round_deadline_utc TEXT,
                expected_qids_json TEXT,status TEXT,created_at_utc TEXT);
            CREATE TABLE paired_attempts (
                run_id TEXT,qid TEXT,arm TEXT,ordinal INTEGER,input_identity_sha256 TEXT,
                status TEXT,started_at_utc TEXT,ended_at_utc TEXT,result_json TEXT,error_code TEXT,
                PRIMARY KEY(run_id,qid,arm,ordinal));
            INSERT INTO paired_runs VALUES ('legacy','a','b','c','2026-10-10T04:25:44Z',
                NULL,NULL,NULL,'running','2026-10-09T20:00:00Z');
            INSERT INTO paired_attempts VALUES ('legacy','q','CHEAP_ONLY',1,'old-input',
                'unknown','2026-10-09T20:00:00Z',NULL,'{"execution_kind":"SIMULATED","original":true}',NULL);
        ''')
    initialize_state(state)
    receipt = attempts_for_qid(state, run_id="legacy", qid="q")["CHEAP_ONLY"]
    assert receipt["status"] == "unknown" and receipt["result"]["original"] is True
    assert receipt["provider_calls"] == []
    assert run_record(state, "legacy")["execution_purpose"] == "COHORT"


def test_run_cannot_close_with_pending_provider_child(tmp_path):
    state = tmp_path / "pending-child.sqlite3"
    _calibration_run(state)
    claim = _calibration_claim(state)
    finish_attempt(state, run_id="cal", qid="calibration:q", arm="CHEAP_ONLY", status="unknown")
    with pytest.raises(PairedContractError, match="pending_attempts"):
        close_run(state, run_id="cal", status="completed")
    finish_provider_send(state, request_id=claim.request_id, status="unknown")
    close_run(state, run_id="cal", status="completed")


def test_native_transport_callback_claims_each_actual_call_once_without_upper_double_claim(tmp_path, monkeypatch):
    state = tmp_path / "native-callback.sqlite3"
    runtime = build_simulated_runtime(state_path=state, run_id="native-callback")
    _register(state)
    gateway = runtime.containers["scifact"].quick_chain.generation_roles["chat_cheap"].gateway
    callback = CurrentProviderSendClaims()
    gateway.transport = SimpleNamespace(send_claim_callback=callback)
    original = gateway.generate
    seen = []
    def generate(messages, **kwargs):
        from backend.app.ports.run_lifecycle import current_attempt
        import hashlib
        request_id = current_attempt.get()
        claim = callback(request_id=request_id, purpose="answer", provider="siliconflow",
            model=gateway.chat_model, role="chat_cheap",
            request_identity_sha256=hashlib.sha256(json.dumps(messages).encode("utf-8")).hexdigest())
        assert provider_send_claim_record(state, request_id=request_id)["status"] == "pending"
        seen.append(claim.request_id)
        result = original(messages, **kwargs)
        callback.settle(request_id=request_id, status="simulated")
        return result
    monkeypatch.setattr(gateway, "generate", generate)
    result = _case(runtime, state)
    assert result["status"] == "PAIRED_INPUTS_VERIFIED" and len(seen) == 2
    children = [child for item in attempts_for_qid(state, run_id="case", qid="q").values()
                for child in item["provider_calls"]]
    assert len(children) == 3 and all(child["status"] == "simulated" for child in children)
    # Context is reset; a background/unscoped callback cannot dispatch.
    with pytest.raises(PairedContractError, match="scope_unavailable"):
        callback.request_id(purpose="answer")


def test_context_callback_helpers_have_independent_stable_purpose_identity(tmp_path):
    state = tmp_path / "scoped-helpers.sqlite3"
    _calibration_run(state)
    callback = CurrentProviderSendClaims()
    bound = SQLiteProviderSendClaims(state, "cal", "calibration:q", "CHEAP_ONLY", "d" * 64)
    with provider_send_claim_scope(bound):
        for purpose in ("understand", "rewrite", "expansion", "answer", "calibration"):
            request_id = callback.request_id(purpose=purpose)
            callback(request_id=request_id, purpose=purpose, provider="siliconflow",
                model="XingChenAGI/Xing4.0-29B", role="chat_cheap", request_identity_sha256="e" * 64)
            callback.settle(request_id=request_id, status="unknown")
        with pytest.raises(PairedContractError, match="no_retry"):
            callback(request_id="helper-retry-denied", purpose="rewrite", provider="siliconflow",
                model="XingChenAGI/Xing4.0-29B", role="chat_cheap", request_identity_sha256="e" * 64)
    children = attempts_for_qid(state, run_id="cal", qid="calibration:q")["CHEAP_ONLY"]["provider_calls"]
    assert len(children) == 5 and {row["purpose"] for row in children} == {
        "understand", "rewrite", "expansion", "answer", "calibration"}


def test_memory_free_runtime_preserves_real_binding_type_and_authorization_fields(tmp_path):
    runtime = build_simulated_runtime(state_path=tmp_path / "typed.sqlite3", run_id="typed-copy")
    original = runtime.containers["scifact"]
    cloned = _memory_free_container(original)
    for name, binding in original.quick_chain.generation_roles.items():
        copied = cloned.quick_chain.generation_roles[name]
        assert type(copied) is GenerationRole and copied is not binding
        assert copied.gateway is binding.gateway
        assert copied.v6_binding is binding.v6_binding and copied.xing_binding is binding.xing_binding
        assert copied.simulated is binding.simulated
        copied.check()


def _reviewfix_clock(monkeypatch):
    from datetime import datetime, timezone
    import eval_center.paired_state as state_module
    class Clock(datetime):
        current = datetime(2026, 10, 9, 22, 0, 0, tzinfo=timezone.utc)
        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)
    monkeypatch.setattr(state_module, "datetime", Clock)
    return Clock


def _reviewfix_register(state, *, deadline="2026-10-10T04:25:44Z",
                        dispatch_deadline="2026-10-09T22:00:01Z"):
    register_run(state, run_id="case", cohort_sha256="a" * 64,
        source_manifest_sha256="b" * 64, code_sha="c" * 64,
        deadline_utc=deadline,
        dispatch_deadline_utc=dispatch_deadline, expected_qids=["q"])
    begin_attempt(state, run_id="case", qid="q", arm="CHEAP_ONLY",
                  input_identity_sha256="d" * 64)


@pytest.mark.parametrize("purpose", ["answer", "understand"])
@pytest.mark.parametrize("cutoff", ["dispatch", "absolute"])
def test_reviewfix_lock_wait_rechecks_time_after_acquiring_sqlite_lock(tmp_path, monkeypatch, purpose, cutoff):
    """Real SQLite contention; controlled SIMULATED clock crosses cutoff during wait."""
    from contextlib import contextmanager
    from datetime import timedelta
    import threading
    import eval_center.paired_state as state_module
    state = tmp_path / "lock-delay.sqlite3"
    clock = _reviewfix_clock(monkeypatch)
    if cutoff == "absolute":
        clock.current = clock.fromisoformat("2026-10-10T04:25:43+00:00")
        _reviewfix_register(state, dispatch_deadline="2026-10-10T04:25:44Z")
    else:
        _reviewfix_register(state)
    original_connect = state_module._connect
    entered, done = threading.Event(), threading.Event()
    outcomes = []
    @contextmanager
    def observed_connect(path):
        with original_connect(path) as connection:
            class Connection:
                def execute(self, statement, *args):
                    if statement == "BEGIN IMMEDIATE":
                        entered.set()
                    return connection.execute(statement, *args)
                def __getattr__(self, name):
                    return getattr(connection, name)
            yield Connection()
    monkeypatch.setattr(state_module, "_connect", observed_connect)
    def worker():
        try:
            claim_provider_send(state, run_id="case", qid="q", arm="CHEAP_ONLY",
                request_id="SIMULATED-lock-delay-" + purpose, input_identity_sha256="d" * 64,
                request_identity_sha256="e" * 64, purpose=purpose, provider="siliconflow",
                model="SIMULATED", role="chat_cheap")
            outcomes.append("CLAIMED")
        except PairedContractError as exc:
            outcomes.append(str(exc))
        finally:
            done.set()
    holder = sqlite3.connect(state)
    holder.execute("BEGIN IMMEDIATE")
    thread = threading.Thread(target=worker)
    try:
        thread.start()
        assert entered.wait(3) and not done.is_set()
        clock.current += timedelta(seconds=4)
        holder.rollback()
        assert done.wait(3)
    finally:
        holder.close()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert outcomes == ["run_deadline_expired"]
    with sqlite3.connect(state) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paired_provider_calls").fetchone()[0] == 0


def test_reviewfix_native_callback_rechecks_consumed_claim_at_send_boundary(tmp_path, monkeypatch):
    from datetime import timedelta
    state = tmp_path / "native-send-cutoff.sqlite3"
    clock = _reviewfix_clock(monkeypatch)
    _reviewfix_register(state)
    bound = SQLiteProviderSendClaims(state, "case", "q", "CHEAP_ONLY", "d" * 64)
    callback, sends = CurrentProviderSendClaims(), []
    with provider_send_claim_scope(bound):
        request_id = callback.request_id(purpose="understand")
        callback(request_id=request_id, purpose="understand", provider="siliconflow",
                 model="SIMULATED", role="chat_cheap", request_identity_sha256="e" * 64)
        clock.current += timedelta(seconds=2)
        with pytest.raises(PairedContractError, match="run_deadline_expired"):
            callback.check_before_send(request_id=request_id)
            sends.append("SIMULATED_SEND")
        # The check consumes no new slot and never releases the existing one.
        callback.settle(request_id=request_id, status="blocked", error_code="run_deadline_expired")
        clock.current -= timedelta(seconds=2)
        with pytest.raises(PairedContractError, match="already_claimed_no_retry"):
            callback(request_id=request_id, purpose="understand", provider="siliconflow",
                     model="SIMULATED", role="chat_cheap", request_identity_sha256="e" * 64)
    assert sends == [] and provider_send_claim_record(state, request_id=request_id)["status"] == "blocked"


def test_reviewfix_upper_wrapper_rechecks_after_claim_before_provider_invocation(tmp_path, monkeypatch):
    from datetime import timedelta
    import eval_center.paired_state as state_module
    state = tmp_path / "upper-send-cutoff.sqlite3"
    runtime = build_simulated_runtime(state_path=state, run_id="reviewfix-upper")
    clock = _reviewfix_clock(monkeypatch)
    # invoke_paired_generation_arm owns begin_attempt.
    register_run(state, run_id="case", cohort_sha256="a" * 64,
        source_manifest_sha256="b" * 64, code_sha="c" * 64,
        deadline_utc="2026-10-10T04:25:44Z", dispatch_deadline_utc="2026-10-09T22:00:01Z")
    original_claim = state_module.claim_provider_send
    def delayed_claim(*args, **kwargs):
        claim = original_claim(*args, **kwargs)
        clock.current += timedelta(seconds=2)
        return claim
    monkeypatch.setattr(state_module, "claim_provider_send", delayed_claim)
    outcome = invoke_paired_generation_arm(runtime.containers["scifact"], "question",
        runtime.scopes["scifact"], arm="CHEAP_ONLY", qid="q", run_id="arm",
        state_run_id="case", conversation_id="arm", cloud_allowed_by_kb=runtime.cloud_allowed_by_kb,
        dispatch_guard=lambda: None, state_path=state, input_identity_sha256="d" * 64)
    assert runtime.containers["scifact"].simulated_provider_calls == []
    receipt = attempts_for_qid(state, run_id="case", qid="q")["CHEAP_ONLY"]
    assert outcome["attempt_status"] == receipt["status"]
    assert receipt["status"] in {"blocked", "failed"}
    assert len(receipt["provider_calls"]) == 1 and receipt["provider_calls"][0]["status"] == "blocked"


def test_reviewfix_asymmetric_dynamic_failure_preserves_frozen_gold_denominator(tmp_path, monkeypatch):
    state = tmp_path / "asymmetric.sqlite3"
    run_id = "reviewfix-asymmetric"
    runtime = build_simulated_runtime(state_path=state, run_id=run_id)
    cohort = [json.loads(line) for line in Path(
        "var/reports/v6-first-evaluation-20261010/cohort_dev24.jsonl").read_text(encoding="utf-8").splitlines()]
    target = next(row["qid"] for row in cohort if row["dataset"] == "longbench-zh")
    original = GenerationRole.invoke
    def invoke(role, envelope, **kwargs):
        claims = CurrentProviderSendClaims._bound()
        if claims.qid == target and claims.arm == "DYNAMIC":
            raise ProviderRequestNotSent("SIMULATED_ASYMMETRIC_DYNAMIC_DENIAL")
        return original(role, envelope, **kwargs)
    monkeypatch.setattr(GenerationRole, "invoke", invoke)
    report = _run(_args(state, run_id=run_id), runtime=runtime)
    assert report["answer_gold_denominator"] == 8
    assert report["answer_gold_frozen_rows"] == 8
    assert report["answer_scoring_by_arm"] == {
        arm: {"evaluated": 7 if arm == "DYNAMIC" else 8,
              "failed": 1 if arm == "DYNAMIC" else 0, "not_scored": 0,
              "not_scored_no_answer_gold": 16}
        for arm in ARMS}
    assert report["status"] == "SIMULATED_PAIRED_COHORT_PARTIAL"
    assert report["qid_count"] == 24 and report["attempt_counts"] == dict.fromkeys(ARMS, 24)
    for arm in ARMS:
        assert report["answer_macro"][arm]["counts"]["character_f1"] == {
            "evaluated": 7 if arm == "DYNAMIC" else 8,
            "unavailable": 17 if arm == "DYNAMIC" else 16}
    rows = load_case_results(state, run_id)
    failed = next(row for row in rows if row["qid"] == target)["arms"]["DYNAMIC"]
    assert failed["answer_scoring_status"] == "NOT_SCORED_ATTEMPT_FAILED"
    assert failed["answer_metrics"] == "NOT_SCORED"


# Batch claims: REAL local SQLite, SIMULATED provider/lease/clock only. Existing
# chat test functions/assertions above remain byte-for-byte unchanged.
from eval_center.paired_state import (ProviderBatchCall, ProviderBatchPlan,
    SQLiteProviderBatchSendClaims, begin_provider_batch_scope)


def _batch_plan(purpose='index_embedding', count=2):
    query = 'a'*64 if purpose!='index_embedding' else None
    calls = tuple(ProviderBatchCall(i, () if purpose=='query_embedding' else ('version-1','version-2'),
        () if purpose=='query_embedding' else ('chunk-1','chunk-2'),
        (query,) if purpose=='query_embedding' else ('a'*64,'b'*64),
        str(i)*64,'f'*64,('chunk-1','chunk-2') if purpose=='rerank' else ()) for i in range(1,count+1))
    return ProviderBatchPlan(purpose,'SIMULATED-provider','SIMULATED-model','c'*64,'d'*64,'e'*64,calls,query)


def _batch_setup(path, monkeypatch, purpose='index_embedding', *, cohort_arm=None):
    import eval_center.paired_state as module
    from datetime import datetime,timezone
    class Clock(datetime):
        current = datetime(2026,10,10,2,0,tzinfo=timezone.utc)
        @classmethod
        def now(cls,tz=None):return cls.current
    monkeypatch.setattr(module,'datetime',Clock)
    kind = 'COHORT' if cohort_arm else 'INDEXING' if purpose=='index_embedding' else 'RETRIEVAL'
    register_run(path,run_id='batch-run',cohort_sha256='a'*64,source_manifest_sha256='b'*64,
        code_sha='c'*64,deadline_utc='2026-10-10T07:44:02Z',expected_qids=['unit-1'],execution_purpose=kind)
    if cohort_arm:
        begin_attempt(path,run_id='batch-run',qid='unit-1',arm=cohort_arm,input_identity_sha256='d'*64)
        arm=cohort_arm
    else:
        arm=begin_provider_batch_scope(path,run_id='batch-run',qid='unit-1',input_identity_sha256='d'*64)
    callback=SQLiteProviderBatchSendClaims(path,'batch-run','unit-1',arm,'d'*64,
        _batch_plan(purpose),'e'*64,lambda lease:None)
    return callback,Clock


def _batch_claim(callback, ordinal=1, *, override=None, send_callback=None):
    purpose=callback.plan.purpose
    args=dict(request_id=callback.request_id(purpose=purpose,call_ordinal=ordinal),purpose=purpose,
        provider=callback.plan.provider,model=callback.plan.model,role='rerank' if purpose=='rerank' else 'embedding',
        request_identity_sha256=callback.plan.calls[ordinal-1].request_identity_sha256,call_ordinal=ordinal)
    args.update(override or {})
    return (send_callback or callback)(**args)


def _batch_summary():return {'result_sha256':'a'*64,'result_ref':'sha256:'+'a'*64,'item_count':2}


@pytest.mark.parametrize('purpose',['index_embedding','query_embedding','rerank'])
def test_batch_native_protocol_and_non_answer_scope(tmp_path,monkeypatch,purpose):
    from backend.app.ports.provider_send_claims import ProviderSendClaims
    callback,_=_batch_setup(tmp_path/'center.sqlite3',monkeypatch,purpose)
    assert isinstance(callback,ProviderSendClaims) and isinstance(CurrentProviderSendClaims(),ProviderSendClaims)
    assert callback.arm == ('INDEXING' if purpose=='index_embedding' else 'SHARED_RETRIEVAL')
    with provider_send_claim_scope(callback):
        claim=_batch_claim(callback,send_callback=CurrentProviderSendClaims())
        CurrentProviderSendClaims().check_before_send(request_id=claim.request_id)
    row=provider_send_claim_record(callback.path,request_id=claim.request_id)
    assert row['status']=='pending' and row['batch_plan_sha256']==callback.plan.fingerprint
    assert row['arm'] not in ARMS and row['purpose']==purpose


@pytest.mark.parametrize('purpose',['query_embedding','rerank'])
def test_batch_query_can_bind_actual_cohort_qid_arm(tmp_path,monkeypatch,purpose):
    callback,_=_batch_setup(tmp_path/'center.sqlite3',monkeypatch,purpose,cohort_arm='DYNAMIC')
    assert _batch_claim(callback).arm=='DYNAMIC'
    with pytest.raises(PairedContractError):
        replace(callback,plan=_batch_plan('index_embedding'))(**dict(request_id='bad',purpose='index_embedding',
            provider='SIMULATED-provider',model='SIMULATED-model',role='embedding',request_identity_sha256='f'*64))


@pytest.mark.parametrize('change',['batch_order','profile','embedding','chunk_order','candidate_order','wire','version'])
def test_batch_plan_change_cannot_claim_a_fresh_ordinal(tmp_path,monkeypatch,change):
    callback,_=_batch_setup(tmp_path/'center.sqlite3',monkeypatch,'rerank')
    _batch_claim(callback)
    plan=callback.plan
    if change=='profile':plan=replace(plan,index_profile_sha256='9'*64)
    elif change=='embedding':plan=replace(plan,embedding_identity_sha256='9'*64)
    else:
        calls=list(plan.calls)
        if change=='batch_order':calls=[replace(calls[1],batch_ordinal=1),replace(calls[0],batch_ordinal=2)]
        elif change in {'chunk_order','candidate_order'}:
            calls[1]=replace(calls[1],chunk_ids=tuple(reversed(calls[1].chunk_ids)),
                candidate_order=tuple(reversed(calls[1].candidate_order)),input_sha256=tuple(reversed(calls[1].input_sha256)))
        elif change=='wire':calls[1]=replace(calls[1],wire_request_sha256='9'*64)
        else:calls[1]=replace(calls[1],document_version_ids=('new-version','version-2'))
        plan=replace(plan,calls=tuple(calls))
    with pytest.raises(PairedContractError,match='provider_batch_plan_identity_conflict'):
        _batch_claim(replace(callback,plan=plan),2)
    assert provider_send_claim_record(callback.path,request_id=callback.request_id(purpose='rerank',call_ordinal=2)) is None


@pytest.mark.parametrize('status',['pending','unknown','succeeded','failed','blocked','simulated'])
def test_batch_used_slot_and_unknown_never_replay(tmp_path,monkeypatch,status):
    callback,_=_batch_setup(tmp_path/'center.sqlite3',monkeypatch)
    claim=_batch_claim(callback)
    if status!='pending':callback.settle(request_id=claim.request_id,status=status,
        result=_batch_summary() if status in {'succeeded','simulated'} else None)
    with pytest.raises(PairedContractError,match='already_claimed_no_retry'):_batch_claim(callback)
    with pytest.raises(PairedContractError,match='request_binding_mismatch'):
        _batch_claim(callback,override={'request_id':'renamed-slot'})
    assert callback.reusable_result(request_id=claim.request_id)==(_batch_summary() if status in {'succeeded','simulated'} else None)


def test_batch_concurrent_connections_slot_only_one_claim(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    callback,_=_batch_setup(tmp_path/'center.sqlite3',monkeypatch)
    barrier=threading.Barrier(8)
    def worker():
        barrier.wait(timeout=10)
        try:return _batch_claim(callback).request_id
        except PairedContractError:return None
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda _:worker(),range(8)))
    assert sum(r is not None for r in results)==1
    with sqlite3.connect(callback.path) as db:assert db.execute('SELECT COUNT(*) FROM paired_provider_calls').fetchone()[0]==1


def test_batch_child_process_crash_committed_claim_is_consumed(tmp_path,monkeypatch):
    callback,_=_batch_setup(tmp_path/'center.sqlite3',monkeypatch)
    script=('import os,sys\nfrom pathlib import Path\n'
        'from eval_center.tests.test_public_paired_runtime import _batch_plan,_batch_claim\n'
        'from eval_center.paired_state import SQLiteProviderBatchSendClaims\n'
        'c=SQLiteProviderBatchSendClaims(Path(sys.argv[1]),"batch-run","unit-1","INDEXING","d"*64,'
        '_batch_plan(),"e"*64,lambda lease:None)\n_batch_claim(c)\nos._exit(19)\n')
    proc=subprocess.run([sys.executable,'-B','-X','utf8','-c',script,str(callback.path)],capture_output=True)
    assert proc.returncode==19,proc.stderr.decode()
    with pytest.raises(PairedContractError,match='already_claimed_no_retry'):_batch_claim(callback)
    assert callback.reusable_result(request_id=callback.request_id(purpose='index_embedding')) is None
    (tmp_path/'crash-command.json').write_text(json.dumps({'argv':proc.args,'exit_code':proc.returncode,
        'execution':'REAL_LOCAL_PROCESS_SQLITE_SIMULATED_PROVIDER_NO_SEND'}),encoding='utf-8')


@pytest.mark.parametrize('change',['deadline','lease','lease_delay','lease_identity'])
def test_batch_final_native_check_after_commit_keeps_consumption(tmp_path,monkeypatch,change):
    from datetime import timedelta
    callback,clock=_batch_setup(tmp_path/'center.sqlite3',monkeypatch)
    claim=_batch_claim(callback)
    if change=='deadline':clock.current=clock.fromisoformat('2026-10-10T07:44:02+00:00')
    elif change=='lease_identity':callback=replace(callback,lease_identity_sha256='9'*64)
    elif change=='lease':callback=replace(callback,lease_validator=lambda lease:False)
    else:
        def delay(lease):clock.current=clock.fromisoformat('2026-10-10T07:44:02+00:00')
        callback=replace(callback,lease_validator=delay)
    with pytest.raises(PairedContractError):callback.check_before_send(request_id=claim.request_id)
    assert provider_send_claim_record(callback.path,request_id=claim.request_id)['status']=='pending'
    if change!='deadline' and change!='lease_delay':
        with pytest.raises(PairedContractError):_batch_claim(callback)


def _batch_legacy_state(path):
    import types
    from eval_center.store import initialize_database
    initialize_database(path)
    module=types.ModuleType('_batch_claim_legacy_frozen')
    sys.modules[module.__name__]=module
    source=Path('var/reports/v6-provider-batch-claims-r1/attempt1/baseline/eval_center__paired_state.py.baseline')
    exec(compile(source.read_bytes(),str(source),'exec'),module.__dict__)
    module.register_run(path,run_id='old',cohort_sha256='a'*64,source_manifest_sha256='b'*64,
        code_sha='c'*64,deadline_utc='2026-10-10T07:44:02Z',expected_qids=['q'],now_utc='2026-10-10T01:00:00Z')
    module.begin_attempt(path,run_id='old',qid='q',arm='CHEAP_ONLY',input_identity_sha256='d'*64,now_utc='2026-10-10T01:00:01Z')
    for i,status in enumerate(('pending','unknown','succeeded','failed','blocked','simulated'),1):
        module.claim_provider_send(path,run_id='old',qid='q',arm='CHEAP_ONLY',request_id='old-'+str(i),
            input_identity_sha256='d'*64,request_identity_sha256='e'*64,provider='SIMULATED',model='SIMULATED',
            role='chat_cheap',call_ordinal=i,now_utc='2026-10-10T01:00:02Z')
        if status!='pending':module.finish_provider_send(path,request_id='old-'+str(i),status=status,
            result={'opaque_hash':'f'*64},error_code='OLD',now_utc='2026-10-10T01:00:03Z')
    with sqlite3.connect(path) as db:
        db.execute('CREATE INDEX retained_child_status ON paired_provider_calls(status)')
    return module


def _batch_old_projection(path):
    with sqlite3.connect(path) as db:
        columns=[r[1] for r in db.execute('PRAGMA table_info(paired_provider_calls)') if r[1] not in
            {'batch_plan_sha256','batch_plan_json','lease_identity_sha256'}]
        return {'runs':db.execute('SELECT * FROM paired_runs ORDER BY run_id').fetchall(),
            'attempts':db.execute('SELECT * FROM paired_attempts ORDER BY run_id,qid,arm').fetchall(),
            'children':db.execute('SELECT '+','.join(columns)+' FROM paired_provider_calls ORDER BY request_id').fetchall(),
            'version':db.execute('PRAGMA user_version').fetchone()[0]}


def test_batch_atomic_migration_preserves_all_old_rows_and_registry(tmp_path):
    from eval_center.paired_state import initialize_state
    from eval_center.store import initialize_database
    path=tmp_path/'migration.sqlite3';_batch_legacy_state(path)
    before=_batch_old_projection(path)
    initialize_state(path);initialize_state(path);initialize_database(path)
    assert _batch_old_projection(path)==before
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.execute('SELECT version FROM paired_schema_migrations').fetchall()==[(3,)]
        assert db.execute("SELECT name FROM sqlite_master WHERE name='retained_child_status'").fetchall()
        assert 'INDEXING' in db.execute("SELECT sql FROM sqlite_master WHERE name='paired_attempts'").fetchone()[0]
        assert db.execute("SELECT COUNT(*) FROM paired_provider_calls WHERE status='unknown'").fetchone()[0]==1
    (tmp_path/'migration-preservation.json').write_text(json.dumps({'before':before,'after':_batch_old_projection(path),
        'real_local_sqlite':'PASS','global_user_version':2,'paired_version':3}),encoding='utf-8')


def test_batch_atomic_migration_process_crash_rolls_back_and_recovers(tmp_path):
    from eval_center.paired_state import initialize_state
    path=tmp_path/'crash.sqlite3';_batch_legacy_state(path);before=_batch_old_projection(path)
    script=('import os,sys,sqlite3\nfrom pathlib import Path\nimport eval_center.paired_state as m\n'
        'original=m._migrate_provider_batch_schema\n'
        'def migrate(db):\n'
        ' def stop(action,a,b,c,d):\n'
        '  if action==sqlite3.SQLITE_DROP_TABLE and a=="paired_attempts":os._exit(23)\n'
        '  return sqlite3.SQLITE_OK\n'
        ' db.set_authorizer(stop)\n original(db)\n'
        'm._migrate_provider_batch_schema=migrate\nm.initialize_state(Path(sys.argv[1]))\n')
    proc=subprocess.run([sys.executable,'-B','-X','utf8','-c',script,str(path)],capture_output=True)
    assert proc.returncode==23,proc.stderr.decode()
    assert _batch_old_projection(path)==before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='paired_schema_migrations'").fetchall()==[]
    initialize_state(path)
    assert _batch_old_projection(path)==before
    (tmp_path/'migration-crash-command.json').write_text(json.dumps({'argv':proc.args,'exit_code':proc.returncode,
        'atomic_rollback_preserved':True,'subsequent_recovery_preserved':True}),encoding='utf-8')


def test_batch_success_summary_reuses_hash_and_rejects_secret_result(tmp_path,monkeypatch):
    callback,_=_batch_setup(tmp_path/'center.sqlite3',monkeypatch)
    claim=_batch_claim(callback)
    with pytest.raises(PairedContractError,match='result_summary_invalid'):
        callback.settle(request_id=claim.request_id,status='succeeded',result={'api_key':'DO_NOT_PERSIST'})
    callback.settle(request_id=claim.request_id,status='succeeded',result=_batch_summary())
    assert callback.reusable_result(request_id=claim.request_id)==_batch_summary()
    second=_batch_claim(callback,2)
    callback.settle(request_id=second.request_id,status='succeeded',result=_batch_summary())
    finish_attempt(callback.path,run_id=callback.run_id,qid=callback.qid,arm=callback.arm,status='succeeded')
    close_run(callback.path,run_id=callback.run_id,status='completed')
    assert run_record(callback.path,callback.run_id)['execution_purpose']=='INDEXING'


def test_normal_paired_cap_is_uniform_and_gold_never_enters_generation(tmp_path,monkeypatch):
    state=tmp_path/'uniform-output.sqlite3'
    runtime=build_simulated_runtime(state_path=state,run_id='uniform-output-SIMULATED')
    _register(state)
    seen=[];original=GenerationRole.invoke
    def observe(self,envelope,**kwargs):
        seen.append((self.role,envelope.max_tokens,envelope.prompt,envelope.context_hash,envelope.identity))
        return original(self,envelope,**kwargs)
    monkeypatch.setattr(GenerationRole,'invoke',observe)
    result=run_public_paired_generation_case(runtime.containers['longbench-zh'],
        PublicCase('q','What is A cost? What evidence supports A cost?',{'GOLD_DOC':1},
            'development',('GOLD_SECRET_DO_NOT_GENERATE',)),runtime.scopes['longbench-zh'],
        run_id='case',dataset_name='longbench-zh',dataset_version='SIMULATED-uniform512',
        state_path=state,shared_identity={k:'d'*64 for k in
            ('corpus_sha256','pool_sha256','qrels_sha256','gold_sha256','index_sha256')},
        cloud_allowed_by_kb=runtime.cloud_allowed_by_kb,dispatch_guard=runtime.dispatch_guard,
        execution_kind='SIMULATED')
    assert len(seen)==3 and all(row[1]==512 and 'GOLD_SECRET_DO_NOT_GENERATE' not in row[2] for row in seen)
    assert len({row[2:] for row in seen})==1
    assert {row[0] for row in seen}=={'chat_cheap','chat_expensive'}
    assert result['answer_output_token_cap']==512 and result['status']=='PAIRED_INPUTS_VERIFIED'
    assert all(cell['attempt_status']=='simulated' for cell in result['arms'].values())


def test_uncapped_counterexample_denied_before_any_provider_claim(tmp_path,monkeypatch):
    state=tmp_path/'uncapped-counterexample.sqlite3'
    runtime=build_simulated_runtime(state_path=state,run_id='uncapped-SIMULATED')
    _register(state)
    original=LangChainQuickChain.invoke
    def adversarial_fixture(self,*args,**kwargs):
        kwargs['settings']=replace(kwargs['settings'],answer_output_token_cap=None)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(LangChainQuickChain,'invoke',adversarial_fixture)
    result=run_public_paired_generation_case(runtime.containers['longbench-zh'],
        PublicCase('q','What is A cost? What evidence supports A cost?',{'source':1},'development',('Gold',)),
        runtime.scopes['longbench-zh'],run_id='case',dataset_name='longbench-zh',
        dataset_version='SIMULATED-adversarial-uncapped',state_path=state,
        shared_identity={k:'d'*64 for k in ('corpus_sha256','pool_sha256','qrels_sha256','gold_sha256','index_sha256')},
        cloud_allowed_by_kb=runtime.cloud_allowed_by_kb,dispatch_guard=runtime.dispatch_guard,
        execution_kind='SIMULATED')
    assert all(cell['attempt_status']=='failed' for cell in result['arms'].values())
    assert not runtime.containers['longbench-zh'].simulated_provider_calls
    with sqlite3.connect(state) as db:
        assert db.execute('SELECT count(*) FROM paired_provider_calls').fetchone()[0]==0


def test_changed_output_cap_cannot_resume_consumed_identity(completed_injected_runtime,monkeypatch):
    import eval_center.public_runner as runner
    state,runtime,first=completed_injected_runtime
    before=load_case_results(state,first['run_id'])
    calls=len(runtime.containers['scifact'].simulated_provider_calls)
    monkeypatch.setattr(runner,'PAIRED_ANSWER_OUTPUT_TOKEN_CAP',513)
    with pytest.raises(PairedContractError):
        _run(_args(state),runtime=runtime)
    assert len(runtime.containers['scifact'].simulated_provider_calls)==calls
    assert load_case_results(state,first['run_id'])==before


def test_shared_cli_cohort_scheduler_full_simulation_and_same_id_resume(tmp_path,monkeypatch):
    """Exercise both CLI phases before the immutable owner cutoff, with SIMULATED providers only."""
    from datetime import datetime as RealDateTime, timezone

    class HistoricalTestClock(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            value=cls(2026,10,9,22,0,0,tzinfo=timezone.utc)
            return value if tz is None else value.astimezone(tz)

    # The configured real-run deadline is already historical in this checkout.
    # This injected clock is test-only; it does not change the profile or any grant.
    monkeypatch.setattr('eval_center.public_runner.datetime',HistoricalTestClock)
    monkeypatch.setattr('eval_center.paired_state.datetime',HistoricalTestClock)
    monkeypatch.setattr('backend.app.application.validation_usd_budget.datetime',HistoricalTestClock)
    state=tmp_path/'shared-cli-cohort.sqlite3'
    run_id='v6-shared-cli-simulated-r1-attempt1'

    first=_run(_args(state,phase='paired-simulate',run_id=run_id))
    assert first['scheduler']=='run_public_paired_cohort'
    assert first['status']=='SIMULATED_PAIRED_COHORT_COMPLETED'
    assert first['qid_count']==24
    assert first['quickchain_invocations']==first['new_quickchain_invocations']==72
    assert first['attempt_counts']==dict.fromkeys(ARMS,24)
    assert first['provider_calls']==first['database_queries']==0
    assert first['current_provider_observations']=={
        'execution_kind':'SIMULATED','real_provider_calls':0,
        'cheap_fixture_calls':48,'deepseek_fixture_calls':24}

    runtime=build_simulated_runtime(state_path=state,run_id=run_id)
    recovered=_run(_args(state,phase='paired-run',run_id=run_id),runtime=runtime)
    assert recovered['scheduler']=='run_public_paired_cohort'
    assert recovered['results_sha256']==first['results_sha256']
    assert recovered['reused_qids']==24
    assert recovered['new_quickchain_invocations']==0
    assert recovered['provider_calls']==0
    assert recovered['current_provider_observations']['real_provider_calls']==0

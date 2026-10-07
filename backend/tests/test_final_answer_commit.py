"""SIMULATED final-commit counterexamples. No real DB/provider/service.

Exercise actual Quick/Smart/service/hardening code with local fixtures, and
record SQL commit attempts separately from a live transaction guarantee.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.adapters.answer_audit import LocalAnswerAudit
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.application.agent_ports import SmartAgentResult
from backend.app.application.answer_service import AnswerService
from backend.app.application.final_answer_commit import FinalAnswerCommitCheck, EXCERPT_PREFIXES
from backend.app.application.knowledge_gateway import EvidenceService, KnowledgeGateway
from backend.app.application.query_router import EvidencePlan, EvidenceTarget
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.evidence import freeze_evidence
from backend.app.domain.scope import Scope
from backend.app.domain.errors import FinalAnswerCommitError
from backend.tests.test_answer_service import RecordingRunStore
from backend.tests.test_caption_fact_guard import prose, frozen
from backend.tests.test_structured_evidence import QUESTION, record, snapshots
from backend.tests.test_version_source_contract import SQLRecorder


def check(answer, chunks=None, **kwargs):
    sources = snapshots(*(chunks if chunks is not None else [record()]))
    plan = EvidenceService().plan(QUESTION).evidence_plan
    return FinalAnswerCommitCheck().check(answer, sources, plan, **kwargs)


def test_b7_normal_exact_numeric_candidate_passes():
    assert check("松林门店2025-02的收入为1230元 [E1]") is None


def test_partial_structured_answer_checks_covered_row_without_inventing_missing_target():
    plan = EvidencePlan("two targets", targets=(EvidenceTarget("松林门店", "收入", "2025-02"),
                                               EvidenceTarget("柏林门店", "收入", "2025-02")), kind="parallel")
    checker = FinalAnswerCommitCheck()
    answer = "松林门店2025-02的收入为1230元 [E1]\n柏林门店：资料中没有足够证据，无法回答这一部分。"
    assert checker.check(answer, snapshots(record()), plan, answer_type="partial") is None
    assert checker.check(answer.replace("1230元", "999元"), snapshots(record()), plan, answer_type="partial") == "UNSUPPORTED_ANSWER"


def test_b8_unknown_citation_fails():
    assert check("松林门店2025-02的收入为1230元 [E99]") == "INVALID_CITATION"


@pytest.mark.parametrize("candidate", ["", " \n "])
def test_empty_answer_is_not_success(candidate):
    assert check(candidate) == "MODEL_EMPTY"


@pytest.mark.parametrize("change", [dict(version_id=""), dict(chunk_id=None), dict(quote=""),
    dict(quote_sha256="0" * 64), dict(locator={"bad": float("nan")})])
def test_snapshot_identity_and_freeze_fail_closed(change):
    source = snapshots(record())[0].model_copy(update=change)
    assert FinalAnswerCommitCheck().check("answer [E1]", (source,), EvidencePlan("q")) == "INVALID_EVIDENCE_SNAPSHOT"


def test_duplicate_label_or_chunk_identity_is_not_freezable():
    source = snapshots(record())[0]
    for other in (source, source.model_copy(update={"label":"E2"})):
        assert FinalAnswerCommitCheck().check("answer [E1]", (source, other), EvidencePlan("q")) == "INVALID_EVIDENCE_SNAPSHOT"


@pytest.mark.parametrize("bad", ["other [E99]", "松林门店2025-02的收入为999元 [E1]"])
def test_b9_b10_fallback_cannot_clear_error_without_final_check(tmp_path, monkeypatch, bad):
    import backend.app.application.answer_hardening as hardening
    chunk = record()
    service = EvidenceService(answer_audit=LocalAnswerAudit(tmp_path))
    monkeypatch.setattr(hardening, "evidence_fallback", lambda *a: bad)
    result = service.finalize_answer("run", "rejected candidate", snapshots(chunk), [chunk], service.plan(QUESTION))
    assert result.error == ("INVALID_CITATION" if "E99" in bad else "UNSUPPORTED_ANSWER")
    assert result.answer == "" and result.rejection is not None


@pytest.mark.parametrize("bad", [
    "其他门店2025-02的收入为1230元 [E1]", "松林门店2025-03的收入为1230元 [E1]",
    "松林门店2025-02的收入为1230万元 [E1]", "松林门店2025-02的收入为999元 [E1]",
])
def test_final_commit_preserves_entity_period_value_unit_checks(bad):
    for answer_type in ("generated", "fallback", "partial"):
        assert check(bad, answer_type=answer_type) == "UNSUPPORTED_ANSWER"


@pytest.mark.parametrize("mutation", ["foreign_header", "formula", "missing_cache", "merged"])
def test_unproved_row_is_not_a_success_via_fallback(mutation):
    from copy import deepcopy
    chunk = record()
    locator = deepcopy(chunk.locator)
    cell = locator["cells"][-1]
    if mutation == "foreign_header": cell["column_headers"] = ["成本（元）"]
    if mutation == "formula": cell.update(formula="=1000+230", cache_status="present")
    if mutation == "missing_cache": cell.update(formula="=1000+230", cache_status="missing")
    if mutation == "merged": cell["merged_anchor"] = "R2C2"
    chunk = replace(chunk, locator=locator)
    assert check("松林门店2025-02的收入为1230元 [E1]", [chunk], answer_type="fallback") == "UNSUPPORTED_ANSWER"


def test_b11_caption_number_is_never_original_numeric_evidence():
    chunk = prose("收入为100元", caption=True)
    checker = FinalAnswerCommitCheck()
    for answer_type in ("generated", "fallback", "partial"):
        assert checker.check("收入为100元 [E1]", frozen(chunk), EvidencePlan("收入？"), answer_type=answer_type) == "UNSUPPORTED_ANSWER"
    assert checker.check(EXCERPT_PREFIXES[0] + "[E1] " + chunk.content,
        frozen(chunk), EvidencePlan("收入？"), answer_type="source_excerpt") == "UNSUPPORTED_ANSWER"


def test_explicit_source_excerpt_requires_exact_frozen_quote():
    sources = snapshots(record())
    checker = FinalAnswerCommitCheck()
    answer = EXCERPT_PREFIXES[0] + "[E1] " + sources[0].quote
    assert checker.check(answer, sources, EvidencePlan("q"), answer_type="source_excerpt") is None
    assert checker.check(answer + "收入为999元", sources, EvidencePlan("q"), answer_type="source_excerpt") == "INVALID_SOURCE_EXCERPT"


def test_b12_length_rejected_even_with_valid_answer_or_available_fallback(tmp_path):
    assert check("松林门店2025-02的收入为1230元 [E1]", finish_reason="length") == "MODEL_OUTPUT_TRUNCATED"
    chunk = prose("缓存用于减少重复读取。")
    service = EvidenceService(answer_audit=LocalAnswerAudit(tmp_path))
    result = service.finalize_answer("run", "缓存用于减少重复读取 [E1]", frozen(chunk), [chunk], service.plan("缓存有什么作用？"), "MODEL_OUTPUT_TRUNCATED")
    assert result.error == "MODEL_OUTPUT_TRUNCATED" and result.answer == "" and result.fallback is None


def test_valid_replacement_fallback_passes_same_contract(tmp_path, monkeypatch):
    import backend.app.application.answer_hardening as hardening
    monkeypatch.setattr(hardening, "evidence_fallback", lambda *a: "松林门店2025-02的收入为1230元 [E1]")
    chunk = record(); service = EvidenceService(answer_audit=LocalAnswerAudit(tmp_path))
    result = service.finalize_answer("run", "bad", snapshots(chunk), [chunk], service.plan(QUESTION))
    assert result.error is None and result.fallback == "EVIDENCE_ONLY_FALLBACK"


@pytest.mark.parametrize("mode", ["quick", "smart"])
def test_all_execution_modes_are_checked_again_at_service_boundary(mode):
    repo = InMemoryRetrievalRepository(); repo.add(record())
    core = KnowledgeGateway(HybridRetriever(repo))
    runs = RecordingRunStore()
    class InvalidSmart:
        def run(self, *a, **kw):
            return SmartAgentResult(kw["run_id"], "completed", "invalid [E99]", citations=("E99",), evidence=snapshots(record()))
    service = AnswerService(knowledge_gateway=core, runs=runs, smart_agent=InvalidSmart())
    if mode == "quick":
        from backend.app.application.knowledge_gateway import AnswerResult, Trace
        service.quick_chain.invoke = lambda *a, **kw: AnswerResult(kw["run_id"], "invalid [E99]", ("E99",), core.plan(QUESTION), Trace(), evidence=snapshots(record()))
    outcome = service.answer({"id":"conv", "knowledge_base_scope":["kb"], "document_scope":[]}, QUESTION, mode)
    assert outcome.error_code == "INVALID_CITATION" and outcome.answer == ""
    assert runs.evidence == [] and all(role != "assistant" for _, role, _ in runs.messages)
    assert "answer.completed" not in runs.event_names()


def test_smart_provider_finish_reason_is_checked_before_persistence():
    runs = RecordingRunStore(); repo = InMemoryRetrievalRepository(); repo.add(record())
    class LengthSmart:
        def run(self, *a, **kw):
            return SmartAgentResult(kw["run_id"], "completed", "松林门店2025-02的收入为1230元 [E1]",
                citations=("E1",), evidence=snapshots(record()), finish_reason="length")
    service = AnswerService(retriever=HybridRetriever(repo), runs=runs, smart_agent=LengthSmart())
    outcome = service.answer({"id":"conv", "knowledge_base_scope":["kb"], "document_scope":[]}, QUESTION, "smart")
    assert outcome.error_code == "MODEL_OUTPUT_TRUNCATED"
    assert not runs.evidence and all(role != "assistant" for _, role, _ in runs.messages)


@pytest.mark.parametrize("cancelled,readable", [(True, True), (False, False)])
def test_b13_repository_cancellation_and_dangling_snapshot_prevent_success(cancelled, readable):
    def handler(sql, params):
        if sql.startswith("SELECT status FROM rag_runs"): return "cancelled" if cancelled else "running"
        if sql.startswith("SELECT content FROM chunks"): return None
    engine = SQLRecorder(handler)
    repository = PostgresKnowledgeRepository(engine, storage=None)
    kwargs = dict(run_id="run", conversation_id="conv", answer="松林门店2025-02的收入为1230元 [E1]",
                  citations=("E1",), snapshots=snapshots(record()), error_code=None, mode="quick",
                  commit_check=lambda: check("松林门店2025-02的收入为1230元 [E1]"))
    if cancelled:
        assert repository.finalize_answer(**kwargs) is False
    else:
        with pytest.raises(FinalAnswerCommitError, match="INVALID_EVIDENCE_SNAPSHOT"):
            repository.finalize_answer(**kwargs)
    assert not any(sql.startswith(("INSERT", "UPDATE")) for sql, _ in engine.calls)


def test_transactional_snapshot_failure_becomes_failed_terminal_without_answer():
    class UnreadableStore(RecordingRunStore):
        def __init__(self): super().__init__(); self.attempts = []
        def finalize_answer(self, **kwargs):
            self.attempts.append(kwargs)
            if kwargs["error_code"] is None:
                assert kwargs["commit_check"]() is None
                raise FinalAnswerCommitError("INVALID_EVIDENCE_SNAPSHOT")
            self.complete_run(kwargs["run_id"], "failed", kwargs["error_code"])
            return True
    repo = InMemoryRetrievalRepository(); repo.add(record())
    runs = UnreadableStore()
    service = AnswerService(retriever=HybridRetriever(repo), runs=runs)
    from backend.app.application.knowledge_gateway import AnswerResult, Trace
    service.quick_chain.invoke = lambda *a, **kw: AnswerResult(kw["run_id"],
        "松林门店2025-02的收入为1230元 [E1]", ("E1",), service.knowledge_gateway.plan(QUESTION),
        Trace(), evidence=snapshots(record()))
    outcome = service.answer(
        {"id":"conv", "knowledge_base_scope":["kb"], "document_scope":[]}, QUESTION)
    assert outcome.error_code == "INVALID_EVIDENCE_SNAPSHOT" and outcome.answer == ""
    assert len(runs.attempts) == 2 and runs.attempts[-1]["snapshots"] == ()
    assert runs.completed == [(outcome.run_id, "failed", "INVALID_EVIDENCE_SNAPSHOT")]
    assert not runs.evidence and all(role != "assistant" for _, role, _ in runs.messages)


def test_sql_success_keeps_message_run_link_and_only_used_frozen_citation():
    import json
    source = snapshots(record())[0]
    answer = "松林门店2025-02的收入为1230元 [E1]"
    policy_calls = []
    def handler(sql, params):
        if sql.startswith("SELECT status FROM rag_runs"): return "running"
        if sql.startswith("SELECT content FROM chunks"): return source.quote
        if "RETURNING next_event_seq-1" in sql: return 1
    def commit_check():
        policy_calls.append(True)
        return check(answer, citations=("E1",))
    engine = SQLRecorder(handler)
    repository = PostgresKnowledgeRepository(engine, storage=None)
    assert repository.finalize_answer(run_id="run", conversation_id="conv", answer=answer,
        citations=("E1",), snapshots=(source,), error_code=None, mode="quick", commit_check=commit_check)
    assert policy_calls == [True]
    evidence = [p for sql, p in engine.calls if sql.startswith("INSERT INTO answer_evidence")]
    messages = [p for sql, p in engine.calls if sql.startswith("INSERT INTO conversation_messages")]
    assert len(evidence) == len(messages) == 1
    assert (evidence[0]["version_id"], evidence[0]["chunk_id"], evidence[0]["quote_sha256"]) == (source.version_id, source.chunk_id, source.quote_sha256)
    assert messages[0]["run_id"] == "run" and messages[0]["content"] == answer
    events = [p["event_type"] for sql, p in engine.calls if sql.startswith("INSERT INTO retrieval_events")]
    assert events == ["retrieval.completed", "evidence.frozen", "answer.completed"]


def test_repository_success_requires_application_commit_contract():
    engine = SQLRecorder(lambda sql, p: "running" if sql.startswith("SELECT status") else None)
    with pytest.raises(FinalAnswerCommitError, match="FINAL_COMMIT_CHECK_REQUIRED"):
        PostgresKnowledgeRepository(engine, storage=None).finalize_answer(
            run_id="run", conversation_id="conv", answer="text", citations=(), snapshots=(), error_code=None, mode="quick")
    assert not any(sql.startswith(("INSERT", "UPDATE")) for sql, _ in engine.calls)

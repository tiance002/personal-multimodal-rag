"""Independent context limits preserve public Top5 and frozen evidence identity."""
import hashlib

import pytest

from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.knowledge_gateway import EvidenceService, KnowledgeGateway
from backend.app.application.quick_chain import LangChainQuickChain
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.config import Settings
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope


SCOPE = Scope.from_ids(["kb"], ["doc"])
FIRST_FIVE = ["c01", "c02", "c03", "c04", "c05"]
FIRST_EIGHT = ["c01", "c02", "c03", "c04", "c05", "c06", "c07", "c08"]


def repository(contents=None, documents=None):
    repo = InMemoryRetrievalRepository()
    texts = contents or [f"alpha 使用步骤{i}。" for i in range(1, 11)]
    for index, text in enumerate(texts, 1):
        doc = documents[index - 1] if documents else "doc"
        repo.add(ChunkRecord(f"c{index:02}", "kb", doc, "version", text,
                             {"kind": "text", "start": index * 100, "end": index * 100 + len(text)},
                             content_sha256=hashlib.sha256(text.encode()).hexdigest()))
    return repo


def expanded(repo, **kwargs):
    return HybridRetriever(repo, top_k=5, candidate_k=32, context_candidate_k=10,
                           context_max_items=8, **kwargs)


def ids(items):
    return [item.chunk.chunk_id for item in items]


class AnswerFixture:
    """Only generation is a fixture; planning, retrieval and validation are real."""
    def __init__(self, answer):
        self.answer_text = answer
        self.prompt = None

    def answer(self, prompt, timeout_seconds):
        self.prompt = prompt
        return self.answer_text


def test_public_top5_is_distinct_from_final_eight_frozen_chunks():
    retriever = expanded(repository())
    result = retriever.retrieve(SCOPE, "alpha")
    assert ids(result.items) == FIRST_FIVE
    assert len(result.context_items) == 10
    assert result.effective_config["context_max_items"] == 8
    service = EvidenceService()
    bundle = service.bundle(service.plan("alpha"), result)
    assert ids(bundle.selected) == FIRST_EIGHT
    citations = CitationService(InMemoryCitationStore())
    frozen = service.with_context(bundle, "run", citations)
    assert frozen.labels == ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8")
    assert [s.chunk_id for s in frozen.snapshots] == FIRST_EIGHT
    assert [s.locator["start"] for s in frozen.snapshots] == [100, 200, 300, 400, 500, 600, 700, 800]
    for snapshot in frozen.snapshots:
        resolved = citations.resolve("run", snapshot.label)
        assert resolved.quote == snapshot.quote
        assert resolved.version_id == "version"
        assert hashlib.sha256(resolved.quote.encode()).hexdigest() == snapshot.quote_sha256
        assert f"[{snapshot.label}] {snapshot.quote}" in frozen.context
    assert "[E9]" not in frozen.context
    assert set(citations.snapshots) == {("run", label) for label in frozen.labels}


def test_generated_e6_and_e8_are_valid_when_actually_in_context():
    model = AnswerFixture("alpha 使用步骤6。 [E6]\nalpha 使用步骤8。 [E8]")
    chain = LangChainQuickChain(KnowledgeGateway(expanded(repository())), answer_gateway=model)
    result = chain.invoke("alpha 有哪些步骤？", SCOPE, run_id="run-valid")
    assert result.error_code is None
    assert result.citations == ("E6", "E8")
    assert [s.chunk_id for s in result.evidence] == ["c06", "c08"]
    assert "[E6] alpha 使用步骤6。" in model.prompt
    assert "[E8] alpha 使用步骤8。" in model.prompt
    assert "[E9]" not in model.prompt


def test_candidate_pool_e9_does_not_authorize_answer_citation():
    chain = LangChainQuickChain(KnowledgeGateway(expanded(repository())),
                              answer_gateway=AnswerFixture("alpha 使用步骤9。 [E9]"))
    result = chain.invoke("alpha 有哪些步骤？", SCOPE, run_id="run-invalid")
    assert result.error_code == "INVALID_CITATION"
    assert result.citations == ()


def test_missing_new_limit_keeps_existing_pool_top5_selection():
    repo = repository()
    old_pool = HybridRetriever(repo, top_k=5, candidate_k=32, context_candidate_k=10)
    baseline = HybridRetriever(repo, top_k=5, candidate_k=32)
    service = EvidenceService()
    old = old_pool.retrieve(SCOPE, "alpha")
    default = baseline.retrieve(SCOPE, "alpha")
    assert "context_max_items" not in old.effective_config
    assert "context_max_items" not in default.effective_config
    assert ids(service.bundle(service.plan("alpha"), old).selected) == FIRST_FIVE
    assert ids(service.bundle(service.plan("alpha"), default).selected) == FIRST_FIVE


@pytest.mark.parametrize("limit", [0, -1, True, False, 1.5, "8", 11])
def test_invalid_context_limit_is_rejected_before_retrieval(limit):
    with pytest.raises(ValueError, match="context_max_items"):
        HybridRetriever(repository(), top_k=5, candidate_k=32,
                        context_candidate_k=10, context_max_items=limit)


def test_context_limit_requires_an_explicit_pool():
    with pytest.raises(ValueError, match="requires context_candidate_k"):
        HybridRetriever(repository(), top_k=5, context_max_items=8)


def test_expanded_pool_obeys_server_document_kb_and_current_version_scope():
    repo = repository()
    for cid, kb, doc, current in [("a-old", "kb", "doc", False),
                                 ("a-foreign", "other-kb", "doc", True),
                                 ("a-other-doc", "kb", "other-doc", True)]:
        repo.add(ChunkRecord(cid, kb, doc, "excluded-version", "alpha " * 100,
                             {"kind": "text", "start": 0}, is_current=current))
    result = expanded(repo).retrieve(SCOPE, "alpha")
    assert ids(result.items) == FIRST_FIVE
    assert ids(result.context_items) == FIRST_EIGHT + ["c09", "c10"]
    assert all(item.chunk.version_id == "version" for item in result.context_items)


@pytest.mark.parametrize("suffix_length,first_fits", [(7990, True), (7991, False)])
def test_8000_character_boundary_includes_frozen_label(suffix_length, first_fits):
    repo = repository(["alpha" + "x" * suffix_length, "alpha short"])
    result = expanded(repo).retrieve(SCOPE, "alpha")
    service = EvidenceService()
    bundle = service.bundle(service.plan("alpha"), result)
    frozen = service.with_context(bundle, "boundary", CitationService(InMemoryCitationStore()))
    assert ids(frozen.selected) == (["c01"] if first_fits else ["c02"])
    assert len(frozen.context) == (8000 if first_fits else 16)
    assert [s.chunk_id for s in frozen.snapshots] == ids(frozen.selected)


def test_label_separator_budget_excludes_second_complete_chunk():
    repo = repository(["alpha" + "x" * 3990, "alpha" + "y" * 3990])
    service = EvidenceService()
    result = expanded(repo).retrieve(SCOPE, "alpha")
    bundle = service.bundle(service.plan("alpha"), result)
    # Each labelled piece is 4000 characters; the two-character separator would
    # make 8002, so the second original quote must not be frozen or clipped.
    frozen = service.with_context(bundle, "separator", CitationService(InMemoryCitationStore()))
    assert ids(frozen.selected) == ["c01"]
    assert len(frozen.context) == 4000
    assert frozen.labels == ("E1",)


def test_soft_document_quota_defers_then_backfills_in_original_rank_order():
    docs = ["doc-a"] * 6 + ["doc-b", "doc-c", "doc-d", "doc-e"]
    result = expanded(repository(documents=docs), context_max_per_document=2).retrieve(Scope.from_ids(["kb"]), "alpha")
    service = EvidenceService()
    selected = service.bundle(service.plan("alpha"), result).selected
    assert ids(selected) == ["c01", "c02", "c03", "c04", "c07", "c08", "c09", "c10"]


def test_soft_quota_does_not_turn_into_hard_same_document_limit():
    result = expanded(repository(), context_max_per_document=2).retrieve(SCOPE, "alpha")
    service = EvidenceService()
    assert ids(service.bundle(service.plan("alpha"), result).selected) == FIRST_EIGHT


def test_merged_passes_keep_legacy_selection_and_report_pool_fallback():
    from backend.app.application.run_metrics import collect_metrics
    retriever = expanded(repository())
    first = retriever.retrieve(SCOPE, "alpha")
    second = expanded(repository([f"alpha other{i}" for i in range(1, 11)])).retrieve(SCOPE, "alpha")
    service = EvidenceService()
    merged = KnowledgeGateway._merge(service.plan("alpha"), first, second, max_items=10)
    with collect_metrics("merge") as metrics:
        bundle = service.bundle(service.plan("alpha"), merged)
    assert ids(bundle.selected) == FIRST_FIVE
    assert not merged.context_items
    assert metrics.hardening["context_pool_fallback"] == "MULTI_PASS_LEGACY"


@pytest.mark.parametrize("enabled", [False, True])
def test_container_default_off_and_opt_in_wiring(enabled, tmp_path):
    from backend.app.bootstrap import build_container
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", storage_root=tmp_path,
                        local_answer_enabled=False, context_pool_enabled=enabled)
    container = build_container(settings, model=None, agent_model=None)
    config = container.knowledge_gateway.retriever.effective_config()
    assert config["top_k"] == 5
    if enabled:
        assert config["context_candidate_k"] == 10
        assert config["context_max_items"] == 8
    else:
        assert "context_candidate_k" not in config
        assert "context_max_items" not in config


def test_environment_opt_in_is_explicit_and_default_off(monkeypatch):
    for key in ["RAG_CONTEXT_POOL_ENABLED", "RAG_CONTEXT_POOL_K", "RAG_CONTEXT_MAX_ITEMS"]:
        monkeypatch.delenv(key, raising=False)
    assert Settings.from_env().context_pool_enabled is False
    monkeypatch.setenv("RAG_CONTEXT_POOL_ENABLED", "true")
    monkeypatch.setenv("RAG_CONTEXT_POOL_K", "8")
    monkeypatch.setenv("RAG_CONTEXT_MAX_ITEMS", "6")
    config = Settings.from_env()
    assert config.context_pool_enabled is True
    assert config.context_pool_k == 8
    assert config.context_max_items == 6


@pytest.mark.parametrize("field,value", [("context_pool_enabled", 1), ("context_pool_enabled", "false"),
    ("context_pool_k", True), ("context_pool_k", 0), ("context_pool_k", -1),
    ("context_max_items", False), ("context_max_items", 0), ("context_max_items", -1),
    ("context_pool_k", None), ("context_max_items", 11)])
def test_direct_settings_reject_invalid_context_configuration(field, value):
    with pytest.raises(ValueError, match="context"):
        Settings(**{field: value})


@pytest.mark.parametrize("name,value", [("RAG_CONTEXT_POOL_ENABLED", "maybe"),
    ("RAG_CONTEXT_POOL_K", "true"), ("RAG_CONTEXT_POOL_K", "-1"),
    ("RAG_CONTEXT_POOL_K", ""), ("RAG_CONTEXT_MAX_ITEMS", "0"), ("RAG_CONTEXT_MAX_ITEMS", "11")])
def test_environment_rejects_invalid_context_configuration(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="context|RAG_CONTEXT"):
        Settings.from_env()


def test_disjoint_retrieval_passes_keep_all_ten_legacy_context_chunks_and_origins():
    from dataclasses import replace
    from backend.app.application.run_metrics import collect_metrics
    from backend.app.ports.providers import EmbeddingResult

    class OfflineEmbedding:
        def embed(self, texts, timeout_seconds):
            return EmbeddingResult([[1.0, 0.0]], "SIMULATED", 2, 0.0, "p2-fixture")

    first = expanded(repository()).retrieve(SCOPE, "alpha")
    second_repo = InMemoryRetrievalRepository()
    for index, chunk in enumerate(repository().records.values(), 1):
        second_repo.add(replace(chunk, chunk_id=f"d{index:02}", version_id="second-version",
                                content=f"alpha new evidence {index}",
                                content_sha256=hashlib.sha256(f"alpha new evidence {index}".encode()).hexdigest(),
                                embedding=(1.0, 0.0), embedding_profile_id="p2-fixture"))
    second = expanded(second_repo, embedding_provider=OfflineEmbedding(), mode="vector").retrieve(SCOPE, "alpha new")
    second_ids = [f"d{index:02}" for index in range(1, 6)]
    assert ids(first.items) == FIRST_FIVE
    assert ids(second.items) == second_ids
    assert len(first.context_items) == len(second.context_items) == 10
    originals = (ids(first.items), ids(second.items), ids(first.context_items), ids(second.context_items))
    service = EvidenceService()
    merged = KnowledgeGateway._merge(service.plan("alpha"), first, second, max_items=10)
    with collect_metrics("p2-disjoint") as metrics:
        bundle = service.bundle(service.plan("alpha"), merged)
        frozen = service.with_context(bundle, "p2-disjoint", CitationService(InMemoryCitationStore()))
    expected = FIRST_FIVE + second_ids
    assert ids(merged.items) == ids(bundle.selected) == ids(frozen.selected) == expected
    assert len(bundle.selected) == 10  # Neither public Top5 nor the new single-pass limit8.
    assert not merged.context_items and not merged.effective_config
    assert merged.sources == ("keyword", "vector")
    assert frozen.labels == tuple(f"E{index}" for index in range(1, 11))
    assert len(frozen.context) < 8000
    assert [s.chunk_id for s in frozen.snapshots] == expected
    assert [s.version_id for s in frozen.snapshots] == ["version"] * 5 + ["second-version"] * 5
    assert metrics.hardening["context_pool_fallback"] == "MULTI_PASS_LEGACY"
    provenance = merged.merge_provenance
    assert provenance["max_items"] == 10
    assert provenance["ordering_semantics"] == "stable_original_then_targeted_unique_bounded_not_reranked"
    assert [row["role"] for row in provenance["passes"]] == ["original", "targeted"]
    assert [row["sources"] for row in provenance["passes"]] == [["keyword"], ["vector"]]
    assert all(row["effective_config"]["context_max_items"] == 8 for row in provenance["passes"])
    assert [row["chunk_id"] for row in provenance["merged_order"]] == expected
    assert [row["selected_from_pass"] for row in provenance["merged_order"]] == [1] * 5 + [2] * 5
    for chunk_id in second_ids:
        origins = provenance["candidate_origins"][chunk_id]
        assert any(row["pass_index"] == 2 and row["ranking"] == "returned_items" for row in origins)
        assert not any(row["pass_index"] == 1 for row in origins)
    assert (ids(first.items), ids(second.items), ids(first.context_items), ids(second.context_items)) == originals


TABLE_QUESTION = "东城门店2025-02的收入（元）是多少？"


def numeric_period_table_repository():
    """SIMULATED native table rows; real row-fact, scope, freeze and validator code."""
    import json
    headers = ["门店", "统计月份", "收入（元）"]
    values = ["东城门店", "2025-02", "1230"]
    text = "Document format: html; table: synthetic-table; row: 2; range: R2C1:R2C3; headers: html-source-policy-v1\n"
    text += " | ".join(f"R2C{column} [{headers[column-1]}]={json.dumps(value, ensure_ascii=False)}; span=1x1"
                       for column, value in enumerate(values, 1)) + "\n"
    cells = []
    for row, displays in [(1, headers), (2, values)]:
        for column, display in enumerate(displays, 1):
            numeric = row == 2 and column == 3
            cells.append({"row": row, "column": column, "coordinate": f"R{row}C{column}",
                          "display": display, "value": int(display) if numeric else display,
                          "value_type": "number" if numeric else "text", "column_headers": [headers[column-1]],
                          "column_header": row == 1, "row_span": 1, "column_span": 1,
                          "cache_status": "not_applicable", "formula": None, "number_format": "General"})
    locator = {"kind": "table", "source_format": "html", "sheet": None,
               "table_id": "synthetic-table", "cell_range": "R2C1:R2C3", "header_rows": [1],
               "header_detection": "html-source-policy-v1", "cells": cells, "quote": text,
               "parse_status": "complete"}
    repo = InMemoryRetrievalRepository()
    for index in range(1, 11):
        repo.add(ChunkRecord(f"c{index:02}", "kb", "doc", "version", text, locator,
                             content_sha256=hashlib.sha256(text.encode()).hexdigest()))
    return repo


def test_e6_and_e8_validate_exact_numeric_period_table_target_in_real_quick_chain():
    from backend.app.application.structured_evidence import row_facts
    repo = numeric_period_table_repository()
    retriever = expanded(repo)
    recalled = retriever.retrieve(SCOPE, TABLE_QUESTION)
    assert ids(recalled.items) == FIRST_FIVE
    assert ids(recalled.context_items) == FIRST_EIGHT + ["c09", "c10"]
    model = AnswerFixture("东城门店2025-02的收入为1230元 [E6]。\n东城门店2025-02的收入为1230元 [E8]")
    service = EvidenceService()
    plan = service.plan(TABLE_QUESTION)
    target, = plan.evidence_plan.targets
    assert (target.subject, target.attribute, target.period, target.unit) == ("东城门店", "收入", "2025-02", "元")
    bundle = service.bundle(plan, recalled)
    assert bundle.decision.accepted and ids(bundle.selected) == FIRST_EIGHT
    result = LangChainQuickChain(KnowledgeGateway(retriever, evidence_service=service), answer_gateway=model).invoke(
        TABLE_QUESTION, SCOPE, run_id="p2-table-valid")
    assert result.error_code is None
    assert result.citations == ("E6", "E8")
    assert [s.chunk_id for s in result.evidence] == ["c06", "c08"]
    assert model.prompt is not None and "[E6]" in model.prompt and "[E8]" in model.prompt
    assert "[E9]" not in model.prompt
    for snapshot in result.evidence:
        chunk = repo.get_chunk(snapshot.chunk_id)
        assert snapshot.quote == chunk.content and snapshot.locator == chunk.locator
        assert snapshot.quote_sha256 == chunk.content_sha256 and snapshot.version_id == "version"
        facts = row_facts(snapshot.quote, snapshot.locator, target, snapshot.quote_sha256, version_id=snapshot.version_id)
        assert len(facts) == 1 and str(facts[0].value) == "1230" and facts[0].unit == "元"


@pytest.mark.parametrize("label", ["E6", "E8"])
@pytest.mark.parametrize("claim", [
    "东城门店2025-02的收入为999元",
    "东城门店2025-03的收入为1230元",
    "西城门店2025-02的收入为1230元",
    "东城门店2025-02的成本为1230元",
    "东城门店2025-02的收入为1230千元",
])
def test_e6_and_e8_reject_unsupported_numeric_period_table_claims(label, claim):
    from backend.app.application.run_metrics import collect_metrics
    model = AnswerFixture(f"{claim} [{label}]")
    chain = LangChainQuickChain(KnowledgeGateway(expanded(numeric_period_table_repository())), answer_gateway=model)
    with collect_metrics(f"p2-table-reject-{label}") as metrics:
        result = chain.invoke(TABLE_QUESTION, SCOPE, run_id="p2-table-reject")
    assert model.prompt is not None and f"[{label}]" in model.prompt
    assert result.error_code == "UNSUPPORTED_ANSWER"
    assert result.answer == "" and result.citations == () and result.evidence == ()
    assert metrics.hardening["validator_rejection"] == "UNSUPPORTED_ANSWER"


def test_table_candidate_e9_cannot_authorize_even_an_exact_numeric_claim():
    model = AnswerFixture("东城门店2025-02的收入为1230元 [E9]")
    result = LangChainQuickChain(KnowledgeGateway(expanded(numeric_period_table_repository())), answer_gateway=model).invoke(
        TABLE_QUESTION, SCOPE, run_id="p2-table-outside")
    assert model.prompt is not None and "[E8]" in model.prompt and "[E9]" not in model.prompt
    assert result.error_code == "INVALID_CITATION"
    assert result.answer == "" and result.citations == () and result.evidence == ()

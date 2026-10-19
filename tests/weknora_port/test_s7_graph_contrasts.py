"""Offline contrast tool identity and frozen-wire contracts; no cloud or DB."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json

import pytest

from scripts.s7_graph_contrasts import SavedRerank, current_rows, load_batches
from tests.weknora_port.test_graph_retrieval import chain, source_row


def pool():
    batch = chain()[0]
    row = source_row(batch.source)
    row.chunk_metadata = {"source_version": "v1"}
    return {"knowledge_base_id": "kb", "documents": [dict(id="first", source_version="v1",
        parse_status="completed", enable_status="enabled")], "rows": [asdict(row)]}


def test_saved_rerank_uses_exact_query_and_order_without_provider_construction():
    model = SavedRerank({"query": "question", "documents": ["first", "second"]},
                         {"results": [dict(index=1, relevance_score=.4), dict(index=0, relevance_score=.2)]})
    with pytest.raises(ValueError, match="INPUT_NOT_IDENTICAL"):
        asyncio.run(model.rerank("question", ["second", "first"], None))
    result = asyncio.run(model.rerank("question", ["first", "second"], None))
    assert [(row.index, row.score) for row in result.results] == [(1, .4), (0, .2)]
    with pytest.raises(ValueError, match="MUST_NOT_RETRY"):
        asyncio.run(model.rerank("question", ["first", "second"], None))


@pytest.mark.parametrize("change", ["disabled", "document_version", "chunk_version", "kb"])
def test_snapshot_scope_version_and_enabled_checks_are_not_bypassed(change):
    data = pool()
    if change == "disabled":
        data["rows"][0]["is_enabled"] = False
    elif change == "document_version":
        data["documents"][0]["source_version"] = "other"
    elif change == "chunk_version":
        data["rows"][0]["chunk_metadata"]["source_version"] = "other"
    else:
        data["rows"][0]["knowledge_base_id"] = "other"
    with pytest.raises(ValueError, match="VERSION_OR_ENABLED_MISMATCH"):
        current_rows(data)


def test_absent_real_extraction_cannot_be_substituted_by_empty_synthetic_graph(tmp_path):
    with pytest.raises(ValueError, match="GRAPH_BUILD_INCOMPLETE"):
        load_batches(tmp_path, {"graph_build_plan": {"selected_documents": ["first"]}}, current_rows(pool()))


def test_build_source_identity_cannot_be_reassigned_to_another_tenant(tmp_path):
    batch = chain()[0]
    path = tmp_path / "first.json"
    path.write_text(json.dumps(dict(tenant_id=1, knowledge_base_id="kb", document_id="first",
        chunks=[dict(source=asdict(batch.source), source_sha256=batch.source.content_sha256,
                     raw_response='{}', request_id="not-sent")])), encoding="utf-8")
    plan = {"tenant_id": 332, "subject_sha256": hashlib.sha256(b"owner").hexdigest(),
            "knowledge_base_id": "kb", "graph_build_plan": {"selected_documents": ["first"]}}
    with pytest.raises(ValueError, match="GRAPH_BUILD_SCOPE_MISMATCH"):
        load_batches(tmp_path, plan, current_rows(pool()))

from __future__ import annotations

import pytest

from backend.app.application.evidence_accumulator import EvidenceAccumulator
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.errors import EvidenceIntegrityError
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope


def test_evidence_accumulator_deduplicates_and_freezes_verified_chunks() -> None:
    chunk = ChunkRecord("chunk-1", "kb", "doc", "version-1", "verified quote", {"kind": "text", "start": 0})
    accumulator = EvidenceAccumulator()

    accumulator.add([chunk, chunk])

    snapshots = accumulator.freeze()

    assert len(snapshots) == 1
    assert snapshots[0].label == "E1"
    assert snapshots[0].chunk_id == "chunk-1"
    assert snapshots[0].version_id == "version-1"


def test_evidence_accumulator_rejects_missing_locator() -> None:
    accumulator = EvidenceAccumulator()

    with pytest.raises(EvidenceIntegrityError, match="locator"):
        accumulator.add([ChunkRecord("chunk-1", "kb", "doc", "version-1", "quote", {})])


def test_search_tool_exposes_the_same_server_label_that_will_be_frozen() -> None:
    chunk = ChunkRecord("chunk-1", "kb", "doc", "version-1", "question evidence", {"start": 0})
    repository = InMemoryRetrievalRepository()
    repository.add(chunk)
    accumulator = EvidenceAccumulator()
    gateway = KnowledgeToolGateway(retriever=HybridRetriever(repository), evidence_accumulator=accumulator)

    result = gateway.invoke("search_knowledge", {"query": "question"}, Scope.from_ids(["kb"]))

    assert result.data["items"][0]["citation_label"] == "E1"
    assert accumulator.freeze()[0].label == "E1"

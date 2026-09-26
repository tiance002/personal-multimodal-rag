import pytest

from backend.app.domain.errors import EvidenceIntegrityError
from backend.app.domain.evidence import EvidenceResolver, freeze_evidence


def test_frozen_evidence_resolves_exact_quote_from_original_version():
    snapshot = freeze_evidence(
        label="E1",
        version_id="ver-1",
        chunk_id="chunk-1",
        quote="原始片段",
        locator={"kind": "text", "start": 0, "end": 4},
    )
    resolver = EvidenceResolver({"E1": snapshot}, lambda version_id, chunk_id: "原始片段")

    resolved = resolver.resolve("E1")

    assert resolved == snapshot
    assert resolved.quote_sha256 == snapshot.quote_sha256


def test_evidence_rejects_changed_source_content():
    snapshot = freeze_evidence(
        label="E1",
        version_id="ver-1",
        chunk_id="chunk-1",
        quote="原始片段",
        locator={"kind": "text"},
    )
    resolver = EvidenceResolver({"E1": snapshot}, lambda version_id, chunk_id: "被篡改片段")

    with pytest.raises(EvidenceIntegrityError):
        resolver.resolve("E1")


def test_evidence_resolver_rejects_unknown_label():
    resolver = EvidenceResolver({}, lambda version_id, chunk_id: "")

    with pytest.raises(EvidenceIntegrityError):
        resolver.resolve("E9")

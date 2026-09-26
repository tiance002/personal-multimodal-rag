from backend.app.application.citations import CitationChunk, CitationService, InMemoryCitationStore


def test_old_citation_resolves_after_new_active_version():
    store = InMemoryCitationStore()
    service = CitationService(store)
    old = CitationChunk("chunk-old", "doc", "kb", "ver-old", "旧版本证据", {"start": 0})
    store.add(old, is_current=True)

    citation = service.freeze("run-1", old)
    store.mark_current("doc", "ver-new")

    resolved = service.resolve("run-1", citation.citation_id)

    assert resolved.version_id == "ver-old"
    assert resolved.current_status == "superseded"

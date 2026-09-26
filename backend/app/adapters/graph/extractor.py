from __future__ import annotations

from backend.app.application.graph import GraphService


class DeterministicStructureExtractor:
    """The V1 extractor delegates to the deterministic document-structure graph."""

    def build(self, repository, document_id: str, version_id: str):
        return GraphService(repository).build(document_id, version_id)

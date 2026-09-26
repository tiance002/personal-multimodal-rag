from __future__ import annotations

from collections.abc import Sequence

from backend.app.application.citations import CitationService
from backend.app.application.retrieval import RetrievalItem


class ContextBuilder:
    def __init__(self, max_chars: int = 8000) -> None:
        self.max_chars = max_chars

    def select(self, items: Sequence[RetrievalItem]) -> list[RetrievalItem]:
        selected: list[RetrievalItem] = []
        used = 0
        for item in items:
            next_label = f"E{len(selected) + 1}"
            piece_length = len(f"[{next_label}] {item.chunk.content}")
            separator = 2 if selected else 0
            if used + separator + piece_length <= self.max_chars:
                selected.append(item)
                used += separator + piece_length
        return selected

    def build(self, run_id: str, items: Sequence[RetrievalItem], citations: CitationService) -> tuple[str, list[str]]:
        pieces: list[str] = []
        labels: list[str] = []
        for item in self.select(items):
            detail = citations.freeze(run_id, item.chunk)
            labels.append(detail.label)
            pieces.append(f"[{detail.label}] {detail.quote}")
        return "\n\n".join(pieces), labels

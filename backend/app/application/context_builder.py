from __future__ import annotations

from collections.abc import Sequence

from backend.app.application.citations import CitationService
from backend.app.application.retrieval import RetrievalItem


class ContextBuilder:
    def __init__(self, max_chars: int = 8000) -> None:
        self.max_chars = max_chars

    def select(
        self,
        items: Sequence[RetrievalItem],
        *,
        max_items: int | None = None,
        max_per_document: int | None = None,
    ) -> list[RetrievalItem]:
        if max_items is not None and (type(max_items) is not int or max_items <= 0):
            raise ValueError("max_items must be a positive integer")
        if max_per_document is not None and (type(max_per_document) is not int or max_per_document <= 0):
            raise ValueError("max_per_document must be a positive integer")

        def fits(selected: list[tuple[int, RetrievalItem]], item: RetrievalItem) -> bool:
            next_label = f"E{len(selected) + 1}"
            piece_length = len(f"[{next_label}] {item.chunk.content}")
            separator = 2 if selected else 0
            used = sum(
                len(f"[E{index + 1}] {selected_item.chunk.content}") + (2 if index else 0)
                for index, (_, selected_item) in enumerate(selected)
            )
            return used + separator + piece_length <= self.max_chars

        selected: list[tuple[int, RetrievalItem]] = []
        deferred: list[tuple[int, RetrievalItem]] = []
        per_document: dict[str, int] = {}
        for index, item in enumerate(items):
            if max_items is not None and len(selected) >= max_items:
                break
            document_id = item.chunk.document_id
            if max_per_document is not None and per_document.get(document_id, 0) >= max_per_document:
                deferred.append((index, item))
                continue
            if not fits(selected, item):
                continue
            selected.append((index, item))
            per_document[document_id] = per_document.get(document_id, 0) + 1

        # The parent cap is soft: use deferred same-document evidence to fill
        # unused slots, preserving multiple passages when they still fit.
        for index, item in deferred:
            if max_items is not None and len(selected) >= max_items:
                break
            if fits(selected, item):
                selected.append((index, item))

        selected.sort(key=lambda indexed_item: indexed_item[0])
        output = [item for _, item in selected]
        # Recheck after restoring original ranking order, since citation labels
        # are assigned in that final stable order.
        while output:
            used = sum(len(f"[E{index + 1}] {item.chunk.content}") + (2 if index else 0)
                       for index, item in enumerate(output))
            if used <= self.max_chars:
                break
            output.pop()
        return output

    def build(self, run_id: str, items: Sequence[RetrievalItem], citations: CitationService) -> tuple[str, list[str]]:
        pieces: list[str] = []
        labels: list[str] = []
        for item in self.select(items):
            detail = citations.freeze(run_id, item.chunk)
            if detail.label in labels:
                continue
            labels.append(detail.label)
            pieces.append(f"[{detail.label}] {detail.quote}")
        return "\n\n".join(pieces), labels

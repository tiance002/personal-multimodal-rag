from __future__ import annotations

from collections.abc import Sequence

from backend.app.application.citations import CitationService
from backend.app.application.retrieval import RetrievalItem


class ContextBuilder:
    def __init__(self, max_chars: int = 8000) -> None:
        self.max_chars = max_chars

    def select(self, items: Sequence[RetrievalItem]) -> list[RetrievalItem]:
        selected, _trace = self.select_with_trace(items)
        return selected

    def select_with_trace(
        self,
        items: Sequence[RetrievalItem],
        *,
        top_k: int | None = None,
    ) -> tuple[list[RetrievalItem], list[dict[str, object]]]:
        """Select context and retain every real decision made by the builder.

        `top_k` is applied here so a trace can distinguish retrieval depth from
        the character budget. There is currently no source deduplication rule;
        same-source chunks remain selectable and therefore never receive a
        fabricated duplicate-occupancy reason.
        """
        if top_k is not None and (type(top_k) is not int or top_k <= 0):
            raise ValueError("top_k must be a positive integer")
        selected: list[RetrievalItem] = []
        trace: list[dict[str, object]] = []
        used = 0
        for position, item in enumerate(items, start=1):
            rank = item.hit.rank if item.hit.rank > 0 else position
            before = used
            label_number = len(selected) + 1
            piece_length = len(f"[E{label_number}] {item.chunk.content}")
            separator = 2 if selected else 0
            reason: str | None = None
            if top_k is not None and rank > top_k:
                reason = "TOP_K_LIMIT"
            elif used + separator + piece_length > self.max_chars:
                reason = "CHAR_BUDGET"
            if reason is None:
                selected.append(item)
                used += separator + piece_length
            trace.append({
                "chunk_id": item.chunk.chunk_id,
                "rank": rank,
                "source_document_id": item.chunk.document_id,
                "source_version_id": item.chunk.version_id,
                "parent_id": item.chunk.document_id.split("#", 1)[0],
                "content_chars": len(item.chunk.content),
                "rendered_chars": piece_length,
                "used_chars_before": before,
                "used_chars_after": used,
                "selected": reason is None,
                "reason": reason,
            })
        return selected, trace

    def build(self, run_id: str, items: Sequence[RetrievalItem], citations: CitationService) -> tuple[str, list[str]]:
        return self.build_selected(run_id, self.select(items), citations)

    def build_selected(self, run_id: str, items: Sequence[RetrievalItem], citations: CitationService) -> tuple[str, list[str]]:
        """Render an already traced selection without selecting it twice."""
        pieces: list[str] = []
        labels: list[str] = []
        for item in items:
            detail = citations.freeze(run_id, item.chunk)
            labels.append(detail.label)
            pieces.append(f"[{detail.label}] {detail.quote}")
        return "\n\n".join(pieces), labels

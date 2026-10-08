from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from backend.app.application.citations import CitationService
from backend.app.application.retrieval import RetrievalItem


class ContextBuilder:
    def __init__(self, max_chars: int = 8000) -> None:
        self.max_chars = max_chars

    @staticmethod
    def _pieces(items, labels=None):
        """Auxiliary context has no E label; only original units are citable."""
        pieces, contexts = [], set()
        for index, item in enumerate(items):
            passage = item.context_passage
            if passage is not None and passage.key not in contexts:
                contexts.add(passage.key)
                pieces.append('[辅助上下文，不是引用证据]\n' + passage.content)
            label = labels[index] if labels is not None else f'E{index + 1}'
            pieces.append(f'[{label}] {item.chunk.content}')
        return pieces

    def _length(self, items):
        return len('\n\n'.join(self._pieces(items)))

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
            return self._length([value for _, value in selected] + [item]) <= self.max_chars

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
                # A whole parent can exceed the general context hard limit.
                # Retain the original whole child instead of slicing evidence.
                if item.context_passage is None:
                    continue
                item = replace(item, context_passage=None)
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
            used = self._length(output)
            if used <= self.max_chars:
                break
            output.pop()
        return output

    def build(self, run_id: str, items: Sequence[RetrievalItem], citations: CitationService) -> tuple[str, list[str]]:
        selected: list[RetrievalItem] = []
        labels: list[str] = []
        for item in self.select(items):
            detail = citations.freeze(run_id, item.chunk)
            if detail.label in labels:
                continue
            labels.append(detail.label)
            selected.append(item)
        context = '\n\n'.join(self._pieces(selected, labels))
        # Actual labels may already exist in a Smart run; never truncate a quote.
        if len(context) > self.max_chars:
            selected = [replace(item, context_passage=None) for item in selected]
            context = '\n\n'.join(self._pieces(selected, labels))
        if len(context) > self.max_chars:
            raise ValueError('CONTEXT_HARD_LIMIT_EXCEEDED')
        return context, labels

from __future__ import annotations

from dataclasses import dataclass

from backend.app.domain.errors import ScopeViolation


@dataclass(frozen=True)
class Scope:
    knowledge_base_ids: frozenset[str]
    document_ids: frozenset[str] = frozenset()

    @classmethod
    def from_ids(cls, knowledge_base_ids: list[str] | tuple[str, ...], document_ids=None) -> "Scope":
        return cls(
            knowledge_base_ids=frozenset(knowledge_base_ids),
            document_ids=frozenset(document_ids or ()),
        )

    def contains(self, knowledge_base_id: str, document_id: str | None = None) -> bool:
        if knowledge_base_id not in self.knowledge_base_ids:
            return False
        if not self.document_ids:
            return True
        return document_id in self.document_ids

    def assert_contains(self, knowledge_base_id: str, document_id: str | None = None) -> None:
        if not self.contains(knowledge_base_id, document_id):
            raise ScopeViolation(
                f"resource is outside the selected scope: {knowledge_base_id}/{document_id}"
            )

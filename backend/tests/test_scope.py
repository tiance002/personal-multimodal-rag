import pytest

from backend.app.domain.errors import ScopeViolation
from backend.app.domain.scope import Scope


def test_scope_allows_only_explicit_knowledge_bases_and_documents():
    scope = Scope.from_ids(["kb-a"], document_ids=["doc-a"])

    assert scope.contains("kb-a", "doc-a")
    assert not scope.contains("kb-b", "doc-b")
    with pytest.raises(ScopeViolation):
        scope.assert_contains("kb-b", "doc-b")


def test_scope_with_no_document_filter_allows_documents_in_selected_kb():
    scope = Scope.from_ids(["kb-a"])

    assert scope.contains("kb-a", "doc-any")

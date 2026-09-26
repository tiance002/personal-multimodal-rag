from __future__ import annotations

import inspect

from backend.app.adapters.postgres.graph_repository import PostgresGraphRepository


def test_graph_query_contains_database_scope_and_readiness_guards() -> None:
    source = inspect.getsource(PostgresGraphRepository.query_graph)

    for required in (
        "ge.document_id IN",
        "JOIN documents d",
        "d.deleted_at IS NULL",
        "d.active_version_id = ge.version_id",
        "dv.index_status = 'ready'",
        "dv.graph_status = 'ready'",
        "kb.graph_enabled = TRUE",
    ):
        assert required in source

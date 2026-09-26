from backend.app.adapters.postgres.schema import M0_TABLES, V2_TABLES


def test_m0_declares_exactly_four_core_tables_and_no_v2_tables():
    assert M0_TABLES == {
        "knowledge_bases",
        "documents",
        "document_versions",
        "ingestion_jobs",
    }
    assert M0_TABLES.isdisjoint(V2_TABLES)

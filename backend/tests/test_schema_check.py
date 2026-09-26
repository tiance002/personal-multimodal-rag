from backend.app.adapters.postgres.schema import M0_TABLES, M1_TABLES, V2_TABLES


def test_m0_declares_exactly_four_core_tables_and_no_v2_tables():
    assert M0_TABLES == {
        "knowledge_bases",
        "documents",
        "document_versions",
        "ingestion_jobs",
    }
    assert M0_TABLES.isdisjoint(V2_TABLES)


def test_m1_declares_the_retrieval_tables_and_stays_disjoint_from_m0_and_v2():
    assert M1_TABLES == {
        "document_sections",
        "chunks",
        "embedding_profiles",
        "chunk_embeddings",
        "chunk_terms",
        "conversations",
        "conversation_messages",
        "rag_runs",
        "retrieval_events",
        "retrieval_hits",
        "answer_evidence",
        "model_calls",
    }
    assert M1_TABLES.isdisjoint(M0_TABLES)
    assert M1_TABLES.isdisjoint(V2_TABLES)

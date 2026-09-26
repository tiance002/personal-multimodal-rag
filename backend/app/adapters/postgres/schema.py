M0_TABLES = frozenset(
    {
        "knowledge_bases",
        "documents",
        "document_versions",
        "ingestion_jobs",
    }
)

M1_TABLES = frozenset(
    {
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
)

V2_TABLES = frozenset(
    {
        "mcp_servers",
        "tool_definitions",
        "tool_policies",
        "approvals",
        "skill_packages",
        "skill_installations",
        "sandbox_runs",
        "artifacts",
    }
)

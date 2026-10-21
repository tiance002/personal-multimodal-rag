# Scoped source-row adapter checkpoint

`storage/knowledge.py` now supplies the `get_chunks(ids, CallContext)` and `get_faq_answers(ids, CallContext)` interfaces consumed by the new Python merge/context pipeline. `hybrid_search` is not yet wired by this checkpoint; it must still connect the existing ParadeDB/vector adapter and fixed fusion/config/FAQ postprocessing.

Source mappings: fixed `internal/application/repository/chunk.go:122-139` (`ListChunksByID`), `knowledgebase_search_results.go:14-117` (source/knowledge loading), `internal/types/faq.go:14-23` (FAQ metadata). The local minimal privacy boundary adds server-resolved owner-to-tenant mapping plus conjunctive KB/document checks; it adds no retrieval scoring or semantic refusal algorithm.

The adapter reads actual current source rows and attached document metadata, preserving source chunk identity, offsets, parent/neighbor links and content revision. It does not substitute vector-index text or model answers as source bodies. FAQ question/answers come from source metadata. Cross-tenant, KB or document rows are rejected even when an injected test driver returns an invalid row. Source retrieval/enrichment stages continue to own their enabled/index-selection behavior; the loader does not add a new result selection strategy.

The generated **unapplied** initial schema now includes `chunks.metadata JSON`, as declared by fixed `types/chunk.go`; its source blob is added to the manifest. Schema generation remains code preparation only. No existing migration receipt or database is rewritten.

Actual commands from the isolated worktree:

- `& 'E:\RAG quention\.venv\Scripts\python.exe' scripts/weknora_port_schema.py --upstream 'E:\RAG-reference\WeKnora-3e8b0bfc'`: exit **0**, generated source schema, **database_writes=0**.
- `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_knowledge_storage.py tests/weknora_port/test_migrate.py --tb=short -p no:cacheprovider`: exit **0**, **8 passed in 0.21s**.

Result **OFFLINE_PASS** for SQL binding, identity/range admission and row conversion only. These tests use fake connections and do not prove PostgreSQL query execution, ranking or citation reread. Actual database migration/read/write, public provider use, production service changes and original-question replay are **NOT RUN**. P2/P3 remain in progress; the full Goal is not complete.

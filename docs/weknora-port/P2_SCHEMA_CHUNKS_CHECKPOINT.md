# P2 isolated schema and source-backed chunk checkpoint

Implemented code preparation only. No PostgreSQL connection, migration, private corpus ingestion, old-service change or real model call was executed. P2 remains incomplete until parser, storage and actual isolated-engine integration are wired and verified.

## Changed files and source mappings

- `scripts/weknora_port_schema.py` extracts the four core retrieval tables and original ParadeDB Chinese tokenizer index from fixed `migrations/paradedb/00-init-db.sql`. It includes the applicable embeddings columns from migrations 000002/000007 and the exact chunk-editing/custom-metadata/revision schema from 000078. It verifies upstream HEAD and records source Git blobs plus generated canonical SQL SHA256. It performs generation only and normalizes line endings/trailing whitespace. No account/login/RBAC/wiki tables are copied.
- `storage/migrations/001_retrieval.sql` and `source_manifest.json` are the generated scoped schema. The immutable source content and prior chunk-revision table are retained. This is not all WeKnora migrations or a claim that every model field is already persisted.
- `storage/migrate.py` revalidates the dedicated connection's database/system identifier before any DDL. Transactional schema receipt and checksum guard precede configured-dimension HNSW indexes using the exact expression, m=16 and ef_construction=64. The runner is explicit, with no old application startup hook or automatic fallback. Its migration receipt/lock is the minimal migration safety wrapper, not a new RAG strategy.
- `storage/chunks.py` converts storage-neutral parsed chunks into fresh source-backed chunk IDs. Fixed mappings: `knowledge_process.go:512-604`, `types/chunk.go:197-210`, `knowledge_index_content.go:12-24`. Parents are persisted and linked but excluded from vector/keyword indexing. Child association uses actual parent rows only; standalone children link in chunk-index order only outside parent-child mode. Evidence retains literal source text; only index input gets the heading breadcrumb/document title. Trusted owner/KB/document/version must match before allocating any IDs.
- `test_migrate.py` and `test_chunk_storage.py` cover admission-before-DDL, wrong instance and checksum, malicious/invalid dimensions, configured HNSW expression, parent indexing exclusion, literal evidence, linking and cross-scope/version rejection.

## Actual verification

Worktree: `C:\Users\22088\.codex\worktrees\weknora-python-port\RAG quention`.

`& 'E:\RAG quention\.venv\Scripts\python.exe' scripts/weknora_port_schema.py --upstream 'E:\RAG-reference\WeKnora-3e8b0bfc'`

Exit **0**: `SCOPED_SCHEMA_GENERATED tables=4 original_chunks_revision_schema=preserved database_writes=0`.

`& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_migrate.py --tb=short -p no:cacheprovider`

Exit **0**: **3 passed in 0.06s**. Fake database boundary only; actual migration **NOT RUN**.

`& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_chunk_storage.py --tb=short -p no:cacheprovider`

Exit **0**: **4 passed in 0.05s**. Pure preparation fixtures only; actual chunk persistence/index visibility **NOT RUN**.

## Required next integration

Dedicated isolated PostgreSQL/ParadeDB execution and tokenizer/ranking comparison remain pending. Original-file/asset storage ownership, document source replacement/cancellation/deletion, chunk persistence/parent expansion bridge, postprocess lifecycle, index-failure cleanup and version switching are not proved by these fake tests. The schema includes applicable core tables and revision support; remaining source-derived fields/tables will be added as their runtime adapters are implemented, not invented as future infrastructure. No original user question replay or citation reread has occurred on this new chain.

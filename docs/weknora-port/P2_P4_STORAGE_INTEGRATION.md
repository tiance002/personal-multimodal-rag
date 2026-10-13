# Storage integration checkpoint

This is Python code in the isolated worktree, not a running service or an applied database migration.

Changed files: `storage/hybrid.py`, `storage/knowledge.py`, `storage/chunks.py`, generated `001_retrieval.sql`/manifest, `storage/history.py`, `storage/connections.py`, `003_common.sql`/manifest, schema generation scripts, and their targeted tests.

The P3 repository bridge loads the tenant-owned knowledge-base configuration before embedding, groups by actual embedding identity, uses the fixed PostgreSQL vector/keyword/RRF and FAQ filtering behavior, and hydrates current source chunks. Parent/image-child hydration is available. Source chunk creation uses fresh IDs and 500-row transactional batches; `source_content` remains empty until the first edit, as upstream does.

Fixed-source correspondence: `internal/application/service/knowledge.go` HybridSearch/CreateChunks; `internal/application/repository/retriever/postgres/repository.go`; `internal/application/repository/chunk.go`; `internal/application/repository/message.go`; `internal/handler/stream.go` ContinueStream; `migrations/paradedb/00-init-db.sql` plus the attributed versioned migrations in the manifests.

The history adapter binds every read/write to the local tenant and subject. It records immutable question Scope, creates the question and empty assistant together, and saves final answer/references/usage/completion in one locked transaction. Duplicate final callbacks cannot replace a completed answer. The Scope snapshots support later outbound provenance checks; they do not broaden retrieval scope. Connection leases verify the physical instance afresh and invalidate their guards on release so parallel retrieval calls do not share one transaction or HNSW GUC scope.

Command (2026-10-13):

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_connection_leases.py tests/weknora_port/test_history_storage.py tests/weknora_port/test_hybrid_storage.py tests/weknora_port/test_knowledge_storage.py tests/weknora_port/test_chunk_storage.py tests/weknora_port/test_migrate.py -q -p no:cacheprovider
```

Exit **0**, **26 passed in 0.35s**. **PASS: offline/scripted SQL boundaries only. NOT RUN: actual PostgreSQL DDL, ranking, locks, transaction rollback, index lifecycle, citation readback, complete API and browser flow.** No physical server identity has been claimed from these doubles. Standalone HybridSearch enrichment remains pending outside the current P3 `skip_context_enrichment` path.

Schema application is still explicit and isolated. `003_common` must precede `002_memory`, because memory configuration and extraction reference the common history tables. No old schema, history, permission or UNKNOWN ledger has been changed. Provider gates and the full lifecycle integration remain pending; this checkpoint is not acceptance of the Goal.

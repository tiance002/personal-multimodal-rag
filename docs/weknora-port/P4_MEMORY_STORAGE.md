# P4 PostgreSQL memory adapter and durable queue

Status: **IMPLEMENTED / OFFLINE SQL-BOUNDARY PASS; actual PostgreSQL NOT RUN**. This worker did not open a database, run DDL/DML against a service, call a provider, start a service or commit. 未实现的命令不得报告通过。

## Changed files and integration boundary

- `backend/weknora_port/storage/memory.py`: `PostgresMemoryRepository` implements all frozen history, memory read/management and maintenance repository methods; `PostgresExtractionEnqueuer` implements durable task enqueue plus scoped claim/ack/failure helpers.
- `storage/migrations/002_memory.sql` and `source_manifest_memory.json`: fixed upstream memory table/schema blocks with precise source blobs/hashes, approved cache/idempotency metadata and PG task-framework adaptation.
- `tests/weknora_port/test_memory_storage.py`: explicitly scripted fake SQL connection; no parser/database engine. The conversation fixtures and reference-only pure-function oracle remain separate checks.
- This report. Other workers' storage, schema001/common003, API, generation, ingestion and retrieval files were preserved.

Worktree: `C:\Users\22088\.codex\worktrees\weknora-python-port\RAG quention`, branch `codex/weknora-python-port`. Readonly reference `E:\RAG-reference\WeKnora-3e8b0bfc` at `3e8b0bfc80b845b2d4b2ed683994748741450a97`. No old database, old Redis/Asynq process or old KB-scoped memory is a dependency.

Root wires an injected async psycopg-compatible `Connection` (mapping rows, `execute/fetchall`, async transaction context) and the existing `IsolationPolicy`. Adapter import never connects. `verify_identity()` delegates to `PostgresRetriever` read-only `current_database()/pg_control_system()` verification. Every read/write requires that exact connection object and matching identity guard to have passed; invalid tenant/owner or old/prohibited system identity prevents memory SQL. A port number alone cannot establish isolation. Root's migration applicator must reverify identity before DDL.

Root applies **001 retrieval -> 003 common app tables -> 002 memory**, under its checksummed migration transaction. This worker supplies 002 but does not edit/run the migration applicator. The source tenants/messages alterations are conditional on the root-owned tables already existing. This is not a reason to silently omit common schema integration.

## Fixed-source correspondence

| Adapter behavior | Source locator |
|---|---|
| Scope predicates, upsert subject, enabled setting, resident/active/live ordering, active count, touch, survivor capacity ranking | `internal/application/repository/memory.go:32–207,210–354`; tenant+subject always precede limit/paging |
| Immutable save, active/pending replacement links, idempotent replay, confirmation conflict | `memory_lifecycle.go:15–123`; pending proposal cannot retire active facts before confirmation |
| Edit invalidates vectors/proposals, supersede linked pending, deletion cleanup | `memory.go:251–322`; original content remains for superseded provenance, physical removal for user deletion |
| Suppression fingerprint/source-message and cap500 | `memory.go:AddTombstone/trimTombstones/ListTombstones/HasTombstone/HasTombstoneForMessage`; only topic/hash/source identity retained, no raw forgotten content |
| Exact PostgreSQL cosine ordering over one owner, model and dimension | `memory_vector.go:rankInDatabase:128–156`; scoped embeddings join items, active/nonexpired and kind filters, halfvec cosine operator and top limit; no memory HNSW candidate window |
| Embedding byte representation/model metadata/upsert/snapshot protection/backlog/sync | `types/memory.go:1389–1435`, `repository/memory.go:UpsertItemEmbedding/ItemsMissingEmbeddings`, `memory_vector.go:writeVectorColumn/SyncVectorColumn` |
| Topic counters/aliases/rename/promoted state, document affinity | `memory.go:BumpTopic/RenameTopic/MarkTopicPromoted/TopTopics/BumpDocAffinity/DocAffinity/TopDocAffinity`; normalized alias identities, latest12 aliases, blank updates preserve document metadata |
| Durable queue revision, legacy boundary import, claim order/cap32, lease and cursor/failure updates | `memory_extraction.go:81–224`; timestamp+ID cursor, revised arrivals remain pending, invalid output third recorded failure enables evidenced skip |
| Extraction delivery schedule and retry allowance | `internal/application/service/memory/extract.go:151–175`; delay supplied by worker, memory task MaxRetry2 |

The adapter retains little-endian float32 BYTEA vectors as source truth and writes the corresponding halfvec value in the same transaction. Literal rendering chooses the shortest round-tripping float32 decimal in plain notation, as source `FormatEmbeddingLiteral`; model ID and dimensions are persisted and filtered. This target uses root001's required pgvector extension; halfvec schema/query execution is **NOT RUN**, and this adapter does not claim the optional SQLite/no-vector fallback is implemented. Reader failure degrades through the conversation module's documented lexical path.

## Privacy, lifecycle and concurrency contracts

Every mutation starts a transaction, upserts the scoped subject if absent and locks it `FOR UPDATE`. Scope is trusted tenant+principal owner, never KB or a client-selected subject. Content/lifecycle mutations clear `block_text` and increment `cache_revision` inside that same transaction. The cache callback locks the subject and writes only when `cache_revision=expected_revision`, returning false for stale rebuilds. Metadata-only touch/queue clocks use the same lock without changing recall content. No UI knob exposes the revision.

Save operation identity is checked under the lock. Successful effects and `memory_operations` record commit together; failures roll back without reserving an operation as success. Replay returns the actual stored identity. A deleted replay result conflicts instead of recreating data. Pending proposals preserve the active target, carry `replaces_id`, and supersede earlier proposals; confirmation checks that the referenced active target remains valid and nonexpired before activation. Manual edit deletes its vector and invalidates proposals based on old content. Expiry/archive/supersede extend cache invalidation.

Deletion atomically creates suppression metadata, handles linked proposals, removes vectors/items and invalidates cache. It rereads the actual row **inside the lock**, suppressing both its latest content fingerprint and the manager's older snapshot when an edit raced with deletion. Bulk clear collects the latest active/pending/archived/superseded-priority suppression candidates under the same lock with source cap500. It then removes all owner vectors/items and invalidates the cache. Tombstone failure aborts deletion; no successful forgetting is reported after a rolled-back transaction. These are the approved minimal privacy wrappers; they do not alter the source recall/ranking algorithm.

Late embedding upsert rereads the scoped item under lock and checks source content/topic plus active/pending lifecycle. Deleted, superseded or edited snapshots cannot restore a vector. The embedding upsert's conflict update additionally matches tenant+subject. Subject locks serialize this check with deletion/replacement. Actual lock/CAS/rollback behavior needs the isolated PostgreSQL runner; scripted fixtures alone cannot prove it.

Extraction state uses source JSON keys `lease_id`/`lease_until` and indexed per-session progress rows. DB `now()` supplies lease time. Subject locks serialize enqueue, claim, checkpoint, failure, finish and release. Claim sorts `updated_at ASC,session_id ASC`, cap32; fallback payload insertion is `DO NOTHING` so duplicate completed jobs cannot reactivate a drained session. Checkpoint never moves backwards, compares timestamp then ID, and clears pending only when captured revision still matches. Invalid model output keeps a recorded range/count; only the third failure for the same starting cursor permits a skip. Another worker's lease cannot checkpoint or release this worker's state.

## Asynq-to-PG delivery adaptation

`memory_extract_jobs` is a **new isolated task-framework adaptation**, not a new memory algorithm. Authoritative pending sessions/cursors and subject worker lease remain the source-shaped tables above. Task payload contains tenant/owner/session/message/model/language identifiers and no question/transcript. There is no automatic polling loop, provider call or old Redis process dependency in this adapter.

Integration helpers:

```python
queue = PostgresExtractionEnqueuer(memory_repository)
await queue.enqueue(payload, delay_seconds, max_retry=2)
job = await queue.claim_due(scope, lease_id, ttl_seconds=600)
await queue.complete_job(job)
await queue.fail_job(job, retry_at=explicit_runtime_retry_timestamp)
```

Only queued duplicate payloads coalesce; a running task can enqueue a successor. Claim is scoped, locks delivery rows and can reclaim expired leases. Completion/failure require matching live delivery lease. Initial delivery plus two retries is the ceiling; exhausted delivery becomes failed while authoritative pending sessions remain. Runtime explicitly supplies retry time; this adapter invents no alternate backoff policy. A queued successor is preserved when a failed running delivery would conflict with it. Terminal task payload is cleared. Root must connect these helpers to the authorized worker runtime; actual delivery scheduling/recovery remains **NOT RUN**.

## Required root-owned common columns

- Default workspace config query: `tenants(id,memory_config JSONB)`. Empty config returns unavailable/default-disabled memory. Optional `config_loader(tenant_id) -> MemoryConfig | None` can supply validated trusted configuration instead.
- History/extraction reads `messages(id,request_id,session_id,tenant_id,subject_id,role,content,rendered_content,created_at,image_captions JSONB,attachments JSONB,knowledge_references JSONB)`. Root003 owns these tables and final-message persistence. Attachments/captions/references map to the frozen conversation DTOs. Owner+tenant+session filters are applied before paging/limits. Extraction compares timestamp+ID; prior context is chronological after newest-page selection.
- Root final commit persists references/used memories and completed status atomically; this adapter does not rewrite answer authority or P6 SSE semantics.

## Checks and remaining acceptance

All commands ran from the isolated worktree; all DB connection responses were synthetic. The fixed-source oracle uses a reference-only Go binary for pure functions, not a product dependency.

| Command | Exit | Result |
|---|---:|---|
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_memory_storage.py -q --tb=short` | 0 | Intermediate **20 passed in 0.16s**; fake SQL boundary only |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_memory_storage.py tests/weknora_port/test_conversation.py tests/weknora_port/test_conversation_maintenance.py tests/weknora_port/test_conversation_oracle.py -q --tb=short` | 0 | Final **70 passed in 14.50s**; 22 scripted SQL-boundary cases plus 48 conversation/maintenance/source cases; actual PG NOT RUN |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m compileall -q backend/weknora_port/storage/memory.py` | 0 | No diagnostics |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -c $memoryBuild`, then `& 'E:\RAG quention\.venv\Scripts\python.exe' -c $manifestUpdate` | 0 | One-time inline local-source transcription bodies recorded in tool trace; `MEMORY_SQL_GENERATION_ONLY; database_writes=0`, then `MEMORY_MANIFEST_UPDATED; database_writes=0`. Source/generated hashes retained in manifest; generation is not migration execution |

Fake checks cover complete frozen protocol methods, unverified/old identity with no memory DML, bound scope parameters, pending preservation, confirmation conflict, operation ordering/replay/rollback, cache CAS, atomically ordered delete/tombstone/vector/cache callbacks, exact pgvector scope/model/dimensions/lifecycle/operator, float32 representation, stale vectors, queue claims/leases/revisions/failure evidence, timestamp+ID history paging, delivery retries/successors, alias retention and current-row suppression during a deletion race. Source manifest checksums are checked against generated SQL.

**NOT RUN:** actual 002 DDL, PostgreSQL SQL parsing/types/operators, rows/constraints/indexes, physical lock concurrency, rollback/CAS fault interleavings, real durable queue/recovery, actual source opening, Gate/provider calls and end-to-end P4. Root's isolated identity runner is the next smallest check. No full P4 acceptance, approved privacy deferral, commit or tag is claimed.

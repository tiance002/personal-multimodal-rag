# Python storage and fusion checkpoint

This is an implementation checkpoint, not completion of P2 or P3. Product code is Python; React/TypeScript remains the frontend. Any Go executable is an offline source oracle only and is not a runtime dependency.

## Changed implementation

- `backend/weknora_port/storage/postgres.py`: injected PostgreSQL connection, explicit new database/server identity check, parameterized ParadeDB keyword query and halfvec vector query. Preserves conjunctive KB/document/tag filtering, historical NULL enabled values, HNSW expression casts, expanded candidate budget, distance threshold and transaction rollback before unsupported-GUC fallback. No connection opens on import; no migration or write is performed by this checkpoint.
- `backend/weknora_port/retrieval/fusion.py`: tenant retrieval configuration getters, nil-vs-explicit-zero rerank threshold, highest-score duplicate handling, keyword score normalization and weighted RRF with first occurrence ranks/vector-preferred metadata. No query-dependent retrieval selector or semantic validator was added.
- `tests/weknora_port/test_storage.py` and `test_fusion.py`: isolated fake-connection boundary and critical pure-function branches. Tests exercise failure admission, scope binding, candidate budgets and configuration distinctions.

## Fixed source mapping

- `internal/application/repository/retriever/postgres/repository.go:164-263` (`KeywordsRetrieve`), `265-488` (`VectorRetrieve`); `structs.go:15-30` (row schema).
- `internal/types/retrieval_config.go:49-115` (effective getters).
- `internal/application/service/knowledgebase_search_fusion.go:38-236` (`fuseOrDeduplicate`, `deduplicateByScore`, `rescaleUnboundedScores`, `fuseWithRRF`).
- Fixed SHA: `3e8b0bfc80b845b2d4b2ed683994748741450a97`; MIT attribution preserved in modules and `UPSTREAM_LICENSE.md`.

## Verification

Worktree: `C:\Users\22088\.codex\worktrees\weknora-python-port\RAG quention`.

Command: `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_storage.py tests/weknora_port/test_fusion.py --tb=short -p no:cacheprovider`

Exit **0**, **13 passed in 0.07s**. Result **OFFLINE_PASS** for these branches only. SQL execution and ParadeDB ranking parity **NOT RUN**; fake connections are not database evidence. No original user question replay, citation reread, provider call, database write, service replacement or deployment occurred.

## Remaining work

P2 migration/schema, versioned document persistence, index batching/ready visibility and actual isolated PostgreSQL/ParadeDB integration remain pending. P3 query understanding/expansion, parallel search, rerank/MMR, merge/parent/neighbors/history, final context serialization and formal pipeline wiring remain pending. This checkpoint does not mount or activate a new API route. Docker was unavailable at the last read-only inspection; no old engine/service was started. The connection adapter requires a pinned connection with mapping rows; creating and verifying that connection belongs to the isolated runner, which is not implemented yet.

The KB scope admission check is the authorized minimal privacy boundary. Empty upstream-internal scopes cannot reach this adapter. This does not alter ranking for an admitted scope. Go map tie order is unspecified; no extra tie policy is asserted in fusion. Later fixed FilterTopK supplies deterministic tie ordering.

## Indexing and TopK follow-up checkpoint

Added `storage/indexing.py`: upstream base64-payload scrubbing and 20,000-rune cap, embedding before index writes, five-attempt ordinary embedding-error backoff, 40-row vector/10-row keyword batches, at most five concurrent saves and sibling cancellation. Each injected embedding attempt receives its attempt index so the provider/cost boundary can allocate a distinct identity. Cancellation and explicit privacy/budget admission denial do not retry. This embedding retry is separate from Cheap chat: Cheap model timeout still has no Cheap retry and permits only one controlled Expensive upgrade.

Extended `PostgresRetriever.batch_save`: verified isolated identity and server-authorized writable KBs required; parameterized halfvec insertion, cleaned invalid Unicode/NUL and `ON CONFLICT DO NOTHING`. No original index/source is overwritten. Live writes remain **NOT RUN**. `psycopg 3.2.10` and `dict_row` import succeeded, exit 0; no connection was opened.

Added `retrieval/types.py` and `filter_top_k.py`: MergeResult precedes RerankResult precedes SearchResult; exact deterministic sort is score descending, **KnowledgeID**, chunk type, chunk index, ID. Zero/negative TopK sorts without truncation; no-retrieval skips the stage. This does not add a new semantic gate or drop a different result list.

Source mappings: `keywords_vector_hybrid_indexer.go:85-237` (BatchIndex, retry, scrub, bounded save), `postgres/repository.go:91-114` (BatchSave), `common/tools.go:189-211` (CleanInvalidUTF8), `chat_pipeline/filter_top_k.go:32-118` (stage and ordering). Important source detail: its embedding retry loop has **no transient-error classifier**, despite its comment; implementation follows executed branches rather than inventing one.

Latest command: `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_storage.py tests/weknora_port/test_indexing.py tests/weknora_port/test_fusion.py tests/weknora_port/test_filter_top_k.py --tb=short -p no:cacheprovider`. Exit **0**, **22 passed in 0.12s**, **OFFLINE_PASS** only. Fixtures use fake embedding and database boundaries. Full-chain ingest, PostgreSQL migration/execution/ranking and provider calls remain **NOT RUN**.

Do not equate index completion with document ParseStatus completed: fixed `knowledge_process.go:214-240` keeps processing while text/multimodal postprocessing remains. Document lifecycle orchestration, original-file ownership/version protection, parent/child persistence, postprocessing and cleanup-on-index-failure remain required integration work; they are not implemented by this checkpoint.

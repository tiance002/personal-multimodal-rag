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

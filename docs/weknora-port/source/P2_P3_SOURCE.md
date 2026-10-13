# WeKnora P2/P3 fixed-source findings

Read-only source audit for upstream `E:\RAG-reference\WeKnora-3e8b0bfc`, fixed at `3e8b0bfc80b845b2d4b2ed683994748741450a97`. Findings below describe source branches and their direct callers. They establish source behavior, not runtime parity or the cause of any user's Run result. Go oracle: **NOT RUN**.

## P2 — chunking, parser dispatch, indexing engines

### Strategy chain and configuration

`internal/infrastructure/chunker/strategy.go:34-58,88-123,273-314` — `Split` and `SplitWithDiagnostics` resolve an ordered strategy chain and return the first strategy output that passes validation; an explicit heading strategy is tried before legacy, explicit heuristic before legacy, while auto/unknown profiles the document and selects its tier. Recursive/legacy resolves straight to legacy. If all strategy output is rejected, the compatibility `SplitText` path is returned as a last resort. Empty text returns no chunks. **Contract:** input `(text, SplitterConfig)` → chunks (and optional selected/rejection diagnostics); explicit strategy does not imply that its output must win.

`internal/infrastructure/chunker/strategy.go:125-185,190-242,250-267` — parent and child are independently passed through `Split`; child split runs on each parent body. A stored parent is emitted when there is more than one child or the single child's content differs from the parent. Otherwise the child has `ParentIndex=-1`. Child start/end are offset by parent start; headings/breadcrumbs are merged. Zero sizes are normalized, and parent/child default derivation uses 4096/384 respectively, child overlap `childSize/5`, with base separators/strategy/token limit/languages carried as specified in `DeriveParentChildConfigs`. These are derivation fallbacks, not universal chunk defaults.

`internal/application/service/knowledge_process_config.go:40-98,253-291,319-327` — effective process config starts from the KB snapshot, then applies only present upload/process overrides; chunking fields merge selectively (nonzero sizes/overlap, nonempty strategy/separators, etc.), while explicit `EnableParentChild=false` is authoritative. Parser engine map merge copies tenant overrides first and upload overrides second, so upload wins per key. `knowledge_process.go:319-338,3811-3859` builds splitter configs and invokes `SplitParentChild` only when the effective flag is enabled; output carries child rune start/end, parent association and separately stored parent text.

### Heading output and offsets

`internal/infrastructure/chunker/heading_splitter.go:32-114,209-255,297-322` — heading split returns legacy splitting if no usable boundaries or a single section is found; otherwise it forms heading sections, records breadcrumb hierarchy, and subdivides oversize sections with `SplitText` while offsetting child boundaries back into the original document. `splitter.go:279-314` and heading/heuristic implementations use Go rune positions for `Start`/`End`; parent-child offsets preserve those positions. A byte-offset port will diverge on non-ASCII text.

### Reader dispatch is separate from heading chunking

`internal/application/service/knowledge_process.go:3911-3970` — ingestion selects a parser engine from tenant/upload overrides plus parser rules and then calls `resolveDocReader`; it builds a reader request with the resolved engine and overrides. This is format parsing, before chunk strategy selection. A parser returning Markdown headings does not itself select the heading chunker; the chunking strategy/profile resolver does that later.

`docreader/parser/registry.py:28-42,49-114,151-213` — requested engine is honored only if registered and supporting the extension; otherwise extension-level default is tried, then builtin, then an unsupported-engine error. Defaults include built-in PDF, DOC/DOCX, XLS/XLSX and images; PPT/PPTX and CSV use MarkItDown. OpenDataLoader is an optional PDF registration guarded by availability; source comments place MinerU handling in Go rather than this Python registry. **Port implication:** dispatch must preserve engine support checks/fallback order and not assume an external reader is installed merely because it is registered conditionally.

### Indexing and database retrieval engines

`internal/application/service/retriever/keywords_vector_hybrid_indexer.go:61-127,129-145` — this engine delegates retrieval to the selected index repository. Single `Index` embeds only when vector retrieval is enabled, then saves the index record either way. `BatchIndex` embeds and saves in batches of 40 with concurrency capped at five; keyword-only batches skip embeddings, save in batches of ten, also capped at five. Batch embedding retries transient errors with backoff. This separates the shared indexing contract from the backend-specific SQL and makes the PostgreSQL extension requirement a backend dependency.

`internal/application/repository/retriever/postgres/repository.go:164-260` — keyword retrieval uses ParadeDB `content ||| ?` any-token matching and `paradedb.score(id)`, applies KB/document scope filters, optional tags and enabled-chunk filtering, sorts score descending, and limits TopK. `:265-433` — vector retrieval uses pgvector/halfvec and an HNSW expression index, applies the same scope and enabled filters, retrieves an expanded candidate pool `clamp(2*TopK,100,200)` (at least TopK), applies distance threshold `1-similarity`, sorts by distance and truncates to TopK; iterative strict-order HNSW scan is best effort. **Resource contract:** this source path requires a ParadeDB-compatible `|||`/`paradedb.score` extension for its keyword semantics and pgvector/halfvec plus HNSW for vector semantics. Source inspection cannot establish which extensions are installed in any target database.

## P3 — retrieval config, expansion, rerank, fusion, merge order

### Config precedence and live caller

`config/config.yaml:9-25` — default conversation values include embedding TopK 30, vector threshold .2, keyword threshold .3, rerank TopK 30, rerank threshold .3, rewrite/query expansion/rerank enabled. `internal/types/retrieval_config.go:14-109` — tenant retrieval-config getters have distinct fallback values (TopK 50, vector .15, keyword .3, rerank TopK 10, rerank threshold .2, RRF K 60 and vector/keyword weights .7/.3). Don't collapse these defaults: they are used by different callers.

`internal/application/service/session_knowledge_qa.go:126-157,209-228` — standard session QA copies conversation config into `ChatManage`, then registers stages in this order: history load, memory recall, query understand, parallel chunk search, rerank, optional web fetch, merge, TopK filter, optional data analysis, chat-message assembly, completion. `knowledgebase_search.go:125-317` is a separate HybridSearch caller: its request MatchCount controls result count; it uses tenant RetrievalConfig for fusion. When unset, normalized MatchCount follows RetrievalConfig getter fallback. Therefore one Python settings object should not silently make YAML, tenant retrieval JSON, and per-request MatchCount interchangeable.

### Hybrid retrieval has three different branches

`internal/application/service/knowledgebase_search_fusion.go:12-53,65-123,125-186` — classify by retriever type; vector-only deduplicates by highest score and preserves vector scores; keyword-only deduplicates and rescales scores into [0,1] only when finite max exceeds 1; hybrid uses weighted reciprocal-rank fusion. RRF uses 1-based ranks and `vectorWeight/(k+vectorRank) + keywordWeight/(k+keywordRank)`, takes the union, prefers vector metadata when both return a chunk, and sorts descending by fused score. Equal scores have no explicit stable tie-break in this function. Candidate golden: same ID in both lists plus vector-only and keyword-only candidates; assert each branch’s returned score and metadata source, and record ties as an unordered equivalence set unless downstream canonical sorting is part of the contract.

`internal/application/service/knowledgebase_search.go:125-300` — HybridSearch scopes the query to KB/document IDs, over-retrieves each engine with `max(matchCount*5, DefaultRetrievalTopK)*numberOfSelectedKBs` (then caps it), calls available retrieval paths, fuses results, truncates to requested MatchCount and enriches results. If both result families are empty it returns no candidates. Scope filters are conjunctive; widening to all KBs as an internal fix would break the source contract.

### Query understand, low-recall expansion

`internal/application/service/chat_pipeline/query_understand.go:62-74,230-321,381-396` — `RewriteQuery` starts as the original query. With rewrite disabled and no images it skips the model. Model selection falls through configured understand model to chat model; no model or parse/model failure proceeds using original query. Images can activate this path even when rewrite is disabled. This stage may transform the query but is not guaranteed to.

`internal/application/service/chat_pipeline/search.go:140-146` — expansion gate is `EnableQueryExpansion && len(SearchResult) < max(1, EmbeddingTopK)`, not only zero results. `query_expansion.go:16-110,114-192` — local expansion (no LLM) generates up to five de-duplicated variants from keyword-only text, quoted phrases, delimiter segments, or question-word removal. It searches each variant across search targets, preserving KB/document/tag scope, with `expTopK=max(2*EmbeddingTopK,2*RerankTopK)`, keyword threshold multiplied by .8, vector threshold unchanged, skip-context-enrichment true and concurrency capped at 16. Initial results are retained and expansion hits are appended for later reranking/merge. Golden: boundary counts `TopK-1`, `TopK`, and 0; assert gate and no extra query at exactly TopK. Add a scope sentinel so expansion never returns a sibling KB/document hit.

### Reranker failure/degradation contract

`internal/application/service/chat_pipeline/rerank.go:38-179,188-220,316-420` — no retrieval, no candidates, empty rerank model ID, or blank passages skips model invocation. Model lookup failure is an error (not fallback). Rerank API failure falls through with original retrieval candidates as `SearchResult`; if successful response filters to no results and original threshold is above .3, retry once at `max(.3, original*.7)`, restoring the original threshold afterward. Threshold filtering retains scores `>= threshold`; if all are below, keep model top-1 only when its score is at least `rerankFallbackMinScore` (.15 generally; 0 for an explicit target recall-threshold override). Returned model results become composite scores and pass through MMR with requested `RerankTopK`. Golden: API exception, model lookup exception, exactly-at-threshold score, retry threshold at .3 floor, top-1 at .149/.15, and scoped override top-1 at 0.

### Merge, parent resolution, history, final TopK

`internal/application/service/chat_pipeline/merge.go:44-98,101-146,149-216,218-255,262-310,312-365` — merge selects nonempty rerank output, otherwise score-sorted search output; deduplicates; injects history references; resolves parent chunks; groups by KB and chunk type, joins sequential current bodies; fills FAQ answers; expands short contexts with neighbors; re-merges and deduplicates exact/partial overlaps. Parent lookup failure preserves original results. Text children resolve to parent-text context; image hits may walk image → text → parent-text and scope image metadata to the matched text child. The order matters: parent resolution happens after initial dedup/history injection and before sequential body merging.

`internal/application/service/chat_pipeline/merge_history.go:12-82` — history uses `RewriteQuery` or original query, tokenizes and Jaccard-filters at .15, excludes IDs already in fresh results, applies score discount .6, relabels as history, and appends at most three. It does not retrieve new DB candidates in this filter; its input comes from prior history stage.

`internal/application/service/chat_pipeline/merge_expand.go:10-19,21-64,67-120,124-210,248-266` — only text chunks shorter than 350 runes are eligible for neighbor expansion; it requires tenant ID and chunk repository; obtains previous/next IDs and loads missing neighbors. It adds neighbors until the merged context reaches 350 runes or no more neighbors exist, and truncates context to 850 runes. It only follows neighbors with the same knowledge ID. Failed base lookup returns original results; failed neighbor lookup is logged and leaves unavailable neighbors out.

`internal/application/service/chat_pipeline/filter_top_k.go:21-69` — final TopK applies to MergeResult if nonempty, else RerankResult, else SearchResult; deterministic sort by descending score with KB ID, chunk type, chunk index and ID tie-breakers, then truncate. This stage follows merge, so search TopK and final answer-context TopK are different limits. `into_chat_message.go:33-145` consumes MergeResult to form final contexts after this filter.

## Golden fixture candidates (offline, deterministic)

1. Parent-child: a single exact-fit child (no stored parent, ParentIndex -1); one parent producing multiple children (stored parent and links); nested heading breadcrumbs; Chinese text with recorded rune offsets; explicit strategy that fails validation and falls through to legacy.
2. Config merge: KB values + partial upload override, explicit `EnableParentChild=false`, and tenant/upload parser engine collision (upload wins); unsupported parser engine falling through to extension default/builtin.
3. Hybrid results: vector-only preserves magnitudes; keyword-only max<=1 preserves values and max>1 normalizes; mixed weighted RRF uses one-based ranks and vector metadata.
4. Expansion: recall count one below threshold activates, count equal threshold does not; max five variants; assert target scope and changed keyword threshold.
5. Rerank: API failure retains original list; lookup failure errors; degraded threshold once only; fallback min scores .15 vs explicit override 0; MMR/TopK order.
6. Merge/history: current and history duplicate IDs, history Jaccard .149/.15, three-history cap, parent context resolution before grouping, neighbor expansion only at 349 runes, and deterministic final TopK tie order.

## Evidence boundary

**PROVEN_CODE_PATH:** the branches, defaults, stage order, SQL operators, score handling, and offsets cited above are directly implemented at the fixed upstream SHA. The P2 Python-port implications are source-derived contracts for equivalence.

**CONDITIONAL_RISK:** installation/availability of ParadeDB, pgvector, reader packages, model availability, DB schema/indexes, observed runtime branches, parity against any live corpus, and the user's actual Run root cause were not observed. No service, database, model, key, or corpus was invoked. No tests or Go oracle were run; report parity as **NOT RUN**, not passed.

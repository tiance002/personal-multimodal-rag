# Current bounded parity matrix — E resume

Fixed upstream3e8b0bfc. Authoritative checkpoint: FIRST_ENTRY_CHECKPOINT_20261013.md. Imported P0 table below is historical scope, not present completion.

| Domain | Python/source mapping | Current evidence / gap |
|---|---|---|
| Entry | http_api→runtime.ApplicationRuntime→storage.turns→service.RuntimeService | Actual nativeHTTP/PG/Redis path PASS, client nonce/server Run relation and scope checked; full Go orchestration paired corpus NOT RUN. |
| Models | source ModelParameters→storage.models→model_registry.SourceCatalogue→neutral ProviderModel |54same-input request-source fixtures with neutral stubs + crypto vector verified; full SDK/production native credential binding parity not claimed. |
| Index | knowledge_process/chunker→documents→storage.chunks/indexing→ParadeDB embeddings | Actual uploaded text/chunks/index/ready/reference and tenant/KB negatives; mock profile worker, synthetic3D vectors; complete ingestion/profile parity pending. |
| RAG/config | query_understand/search/rerank/merge_history/filter_top_k/into_chat_message→retrieval modules | Actual entry executes core, session settings override startup; type fallback stays in existing hybrid. Full Go corpus/tokenizer comparison pending. |
| P5 | original six-feature max(q0,rewrite)/threshold0.35→generation + durable lifecycle |d9c5cbc timeoutOFF override authoritative; length/empty/invalidhandle one upgrade tests; actual truncation2attempts and timeout1UNKNOWN. Actual estimator pending. |
| History/memory | common.go raw query+separate refs→conversation/storage.memory→service/runtime | Actual history reads/rendered sink and source references preserved; auto extraction/vector/maintenance complete entry pending. |
| SSE/citations | source Redis/ContinueStream/source registry→streaming/references/http_api | Actual quoteGET/replay/no-extra-send/terminal once; baseline/Expensive streaming currently overbuffered, timed parity needs repair; real React/cancel/cursor pending. |
| Evaluation/cutover | existing six public adapters→new full-chain paired bridge | P7 not implemented; P8prep pending; real providers/cutover AWAITING_OWNER_APPROVAL. |

## Imported P0 matrix (historical)

# Fixed source → Python behavior matrix

Upstream `3e8b0bfc80b845b2d4b2ed683994748741450a97`. This table records scope, not completed implementations. A verified Git blob is not behavioral parity. Stage reports must replace PENDING with observed inputs, branches, outputs and tests before declaring parity.

| Feature/stage | Fixed source:function / branch | Existing Python reference | New Python destination | Effective config/precedence | Evidence / deviation status |
|---|---|---|---|---|---|
| Provider budget | `api/options.go:CompletionBudget`, `openaicompletions/request.go` renderer | old cloud/service capacity (historical only) | `backend/weknora_port/models/` | completion budget preferred; vendor chooses sole output field | P1 source read; implementation in progress; old probes excluded |
| Transport | `api/transport.go:WithLLMTimeout/envDurationSeconds` | old cloud socket forks | unified async HTTP transport | fallback300chat/600stream; supplied deadline preserved; connection limits separate | Source read; mock timeout/cancel parity pending |
| Usage/cache | `api/usage.go:OpenAIUsage.ToTokenUsage` | service usage/legacy ledger reference | unified models usage plus minimal safety wrapper | documented cache-accounting variants | Source read; absent usage UNKNOWN is safety adaptation |
| Stream assembly | `api/sse.go`, `stream_assembler.go`, completions client | old ordinary_chat_stream (historical only) | unified models stream parser | same vendor/model protocol for both generation roles | P1 mocks planned, Go oracle NOT RUN |
| Parsing | `docreader/parser/registry.py` and concrete parsers | old parsing adapters | `backend/weknora_port/ingestion/` | actual format and OCR/VLM dispatch | P2 source investigation, parse/output/error fixture pending |
| Chunking | `chunker/strategy.go`, splitter/heading/heuristic files | old ParentChild/chunking code | ingestion chunking | Auto/Heading/Heuristic/Recursive resolution; parent-child conditional | P2 exact function/fixture mapping pending;4096/384 not universal defaults |
| Indexing/keywords | hybrid indexer and PostgreSQL repository | custom chunk_terms BM25 | new isolated storage/indexing | same ParadeDB engine first; existing DB never extended | SEARCH_ENGINE_PARITY_DIFFERENCE open; isolated DB test pending |
| Hybrid fusion | `knowledgebase_search_fusion.go` | old custom RetrievalRouter | retrieval fusion | vector-only preserves score, keyword-only rescales, hybrid WeightedRRF | P3 branch/source mapping pending; old selector excluded |
| Retrieval config | `types/retrieval_config.go:GetEffective*`; config YAML | old policy defaults | retrieval config resolver | caller source/request/tenant precedence, nil differs from zero | Source expectations captured; precedence behavioral tests pending |
| Query | pipeline query_understand/search/query_expansion; QA registration | old QueryUnderstand and L0/L1 | new chat_pipeline | actual EnableRewrite/image and expansion-count conditions | P3 exact caller/branch mapping pending |
| Rerank/merge/context | pipeline rerank/merge/merge_expand/merge_history/filter_top_k/into_chat_message | old QualityGate/context8000 | new chat_pipeline | merge before final TopK; source templates and fallback | P3 deterministic candidates/context tests pending |
| Short history | load_history/merge_history/into_chat_message | old completed-history barriers | chat_pipeline history | upstream rounds/compression/reference composition | P4 source/function mapping pending; no old history auto-injection |
| Long memory | memory service/extract/search/vector/consolidate/scope | old GLOBAL/SCOPED memory | new memory/storage | workspace/owner enablement, lexical/vector/consolidation/deletion | P4 isolated lifecycle test pending |
| Citations | `modelcontext/citations.go`, pipeline references | old E1 + forced semantic validator | source handle registry | validate source handles and controlled rendering | P4/P6 fixture pending; no old RegexValidator in success gate |
| SSE/React | Redis manager/GetEvents, session ContinueStream, Vue stream/progress | existing React app and SSE | new FastAPI events + React bindings | exact Run identity, cursor continuation, no regenerated POST | P6 parity pending; no Vue runtime |
| P5 selection | original `rule_router_v1.py:decide`, feature extractor | exact same baseline algorithm | `backend/weknora_port/routing/` |0.35; max(q0,rewrite); verified input-load estimator | Authorized deviation;18legacy snapshots frozen; full P5 replay pending |
| P5 upgrade | owner decision table and latest timeout update | old escalation flags only as reference | new routing coordination | maxCheap1+Expensive1; length/empty/invalidhandle; model timeout OFF (latest 2026-10-13 authority); generic fallback off | Authorized deviation; candidate buffering needed, no streamed failed candidate as final |
| Six datasets | existing public adapters and Gold boundaries | eval_center preserved | separate new-chain evaluation runner | same prepared evidence for paired roles; eligible metrics only | P7 representative fixtures and performance budget pending |

Every P2–P7 row still marked pending must be implemented and verified. P0 does not declare a port complete.

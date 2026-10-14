# WeKnora Python / React learning snapshot

这是给初学者配合 ChatGPT、VS Code 学习的精选源码。Source commit: `9a604666f557dbcc3d0940f7c0b5b2964147b76f`. Fixed upstream: Tencent/WeKnora `3e8b0bfc80b845b2d4b2ed683994748741450a97`.

本分支是新的根提交，不带开发仓库的祖先历史、旧实现、运行数据库、用户语料、截图、请求/readback和历史日志。核心新后端及React组件保留原Git字节；外围改动和每个原文件的hash见 `SOURCE_MANIFEST.json`。开发尚未全部结束，这不是部署或完整上游等价验收。

## Start learning / 离线练习

1. 把 `CHATGPT_LEARNING_PROMPT.md` 发给能读取此分支源码的 ChatGPT；一次一课，要求文件+函数+行号。
2. Python 3.11+：`python -m venv .venv`，激活后 `python -m pip install -e ".[dev,parsers]"`。这些是安装指令，快照交付不声称从零安装已验证。
3. `python -B scripts/check_snapshot.py` 只做syntax/import；`python -B -m pytest tests/weknora_port/test_image_configuration.py tests/weknora_port/test_image_processing.py tests/weknora_port/test_http_api.py tests/weknora_port/test_http_consent.py tests/weknora_port/test_cors_delete.py -q -p no:cacheprovider` 使用fake存储/MockTransport，没有真实模型/DB。
4. `cd frontend; npm ci; npm run build`。`npm run dev` 或仓库根 `./scripts/start_frontend.ps1` 仅启动既有React开发UI。它需要用户另外提供后端才能完成在线问答；默认通过Vite同源 `/api` proxy，避免把跨源PATCH未覆盖当作通过。

## Backend runtime boundary

The backend is an explicit assembly library: `http_api.create_app(runtime, resolve_principal=...)` and `runtime.ApplicationRuntime(...)` require injected verified PG connection leases, Redis stream store, principal, parser/storage dependencies and provider model factory. There is intentionally no auto-connect/auto-migrate/provider-probe script. The original machine-specific `wk.ps1` and DB-writing test service harness are excluded, rather than publishing their credential/path assumptions.

真实运行需要显式建立新隔离PostgreSQL（pgvector + ParadeDB pg_search）、Redis、迁移001–010、原件storage、模型配置及scope/egress/monthly authority；学习测试用SQL doubles，不是数据库验收。缺少授权不应发请求。不要在旧业务资源上应用这些SQL。

Parser extras are separate from OCR: PDF requires PDFium; Office uses optional libraries. Local Tesseract uses PyMuPDF and user-provided `eng`/`chi_sim` traineddata via `RAG_TESSDATA_DIR` (not distributed here). Full HTML OCR uses the pinned local codec source in `backend/weknora_port/ingestion/html_codec_sources/`; build its library tool separately and supply absolute `WEKNORA_HTML_CODEC_PATH` and exact SHA256. No prebuilt codec or OCR data is uploaded. Real provider quality is NOT RUN.

## Two real source chains / 两条核心链

```text
Send → React runPortChat → MessageCreate → OrdinaryTurnRequest
→ accept/SQL user+empty assistant+port_turns
→ history/memory → understand_query → search → rerank → merge → filter_top_k/context
→ GenerationRequest → P5/coordinator → provider stream → Redis events
→ atomic SQL answer/references → terminal SSE complete → React

Upload → multipart → DocumentLifecycleService → immutable source + knowledges
→ direct parser or durable port_image_tasks → chunks/parent-child
→ optional OCR/caption → batch index/embedding → profile/summary → finalize
```

## File/function reading index

Core files retain original line numbers. The simplified `frontend/src/main.tsx` is new; read its own lines. Future tools must check lines rather than assume they never move.

| Topic | File, function, source line range |
| --- | --- |
| React entry/send | `frontend/src/main.tsx`; `frontend/src/weknora_port/WeKnoraPythonApp.tsx` `submit` 259–276, `run` 227–243 |
| HTTP fetch/SSE | `frontend/src/weknora_port/sse.ts` `runPortChat` 112–155, GET-only resume 73–109; `api.ts` for sessions/upload |
| DTO/route | `backend/weknora_port/http_api.py` `MessageCreate` 92–98, `create_message` 568–579; upload 491–507 |
| Internal request/admission | `service.py` `OrdinaryTurnRequest` 20–34, `accept_turn` 139–159; `storage/turns.py` `accept` 76–109 |
| Preparation | `service.py` `prepare_turn` 256–308; `retrieval/pipeline.py` `run` 77–106 |
| Rewrite | `retrieval/query.py` `understand_query` 120–176; starts with original query; parse failure retains it |
| Query embedding/search | `runtime.py` `LeasedKnowledgeRepository._embed` 131–145; `storage/hybrid.py` 73–76; `retrieval/search.py` expansion 198–208 |
| Generation | `generation/coordinator.py` `run` 243–315; `generation/router.py` `decide` 44–56 |
| Persistence/SSE | `storage/generation.py` claim/begin/finish; `storage/history.py` `commit_final` 87–113; `streaming/replay.py` `finalize_turn` 39–44 |
| Upload | `documents.py` `upload_document` 252–308, `_process_document` 310–418 |
| Parent/Child/index | `ingestion/core.py` `ingest_document` 547–562; `storage/chunks.py` `prepare_chunks` 67–110; `storage/indexing.py` `batch_index` 75–105 |
| Image roles/task fence | `image_configuration.py`, `image_multimodal.py`, `image_tasks.py`; `documents.py` locks 443–483, `_mark` 562–590 |
| History/memory | `conversation/history.py` 42–75; `conversation/memory.py` `recall`; `conversation/extraction.py` `schedule` 70–82; `runtime.py` `record_turn_memory` 419–427 |
| Database structure | `storage/migrations/001_retrieval.sql`, `003_common.sql`, `004_generation.sql`, `007_turns.sql`, `010_image_tasks.sql` |

### Objects and tables

- HTTP DTO is a validated JSON envelope, not a database row. The server resolves the trusted principal/scope; message JSON cannot choose tenant identity.
- Browser `client_request_id` handles duplicate POST; server `run_id` groups user/assistant messages. Admission writes both `messages` rows and `port_turns` before model execution. `content` holds raw question/answer; `rendered_content` holds assembled prompt context; `knowledge_references` is JSONB. `generation_runs` and `generation_attempts` record final-generation execution separately.
- Knowledge hierarchy: `knowledge_bases.id → knowledges.knowledge_base_id → chunks.knowledge_id`; `parent_chunk_id` links children to broader source text. `embeddings` links chunks and stores indexed text plus halfvec; it has no tenant column, so scoped source joins matter. Most knowledge IDs are logical relations; not every relation is a SQL foreign key.
- `source_version` is in document/chunk metadata and immutable paths, not a separate document-version table. Original path: `storage_root/tenant/kb/document/source_version/original/filename`. Image task rows store version/config/claim/status, not keys.
- Query embedding uses `rewrite_query` (original if rewrite disabled/failed), not every historical message. Vector-disabled/no-retrieval branches use none; scoped recalls and expansions can use more than one. Upload embedding is a separate batched path: searchable child text plus enabled OCR/caption/summary indexes; parent rows are for context expansion.
- History reads same-session raw questions/answers. Memory is separate owner-scoped tables and optional recall/extraction; auto extraction requires enabled + auto mode + queue. It is not a guaranteed model call for every turn.
- P5 runs after final context. Threshold .35 and max-per-feature(original,rewrite) select Cheap/Expensive. Only truncation/empty/invalid references allow one Cheap upgrade; timeout cannot. Final Context is shared. UNKNOWN remains reserved.
- SSE reads Redis events. Agent-query/progress precede generation; public streams may emit answer before final references; dynamic Cheap draft is buffered. Terminal complete follows SQL answer/reference commit. GET reconnect does not generate a new answer.

## Verification boundaries

The source project recorded 77 targeted offline checks, 11 isolated PostgreSQL/image checks and 1 legacy runtime integration at implementation commit `9d5ea7f...`, with source/hash receipts sealed by `9a604666...`. These are **source-project results**, not new acceptance runs of this learning snapshot. Original logs/receipts/readbacks are deliberately not published here. `SNAPSHOT_VALIDATION.json` separately records only checks actually performed on this snapshot.

Real-provider quality, full fixed-Go orchestration equivalence, all parser environments and production/cutover remain unverified. The included tests are selected synthetic offline tests; live integration harnesses are not included. For public engineering scope see `docs/SCOPE.md`.

## Attribution and license boundaries

Preserve the per-directory NOTICE/LICENSE files and `docs/weknora-port/UPSTREAM_LICENSE.md`; reference code is MIT Tencent/WeKnora. This is not a blanket relicensing of all dependencies. PyMuPDF has its own licensing requirements described in `docs/NOTICE-PyMuPDF.md`; see tokenizer and html-to-markdown licenses too.

## Remaining development work / 仍未完成

These owner-reported boundaries belong to the ongoing source project. The learning snapshot does not implement them or label the refactor complete. Normal-chain code and bounded tests are suitable for study; exception recovery and configuration may change.

- P1: generic provider-error channel behavior, decoder tail flushing/thinking closure and proxy parity still have gaps.
- P4: nil versus zero/session/request/locale/prompt precedence, memory queue lease/crash/archive/CAS semantics and document-affinity UI remain incomplete.
- P3: direct fixed GoJieba/CGo oracle and complete Go ordinary-entry paired comparison are not complete.
- P7: Omni geometry wiring, an independent QA overlay, and MML page-base decisions remain open; no evaluation corpora are included here.
- P8: retiring the old entry and building/accepting the latest source wheel remain unfinished.

Do not infer production readiness from passing source or snapshot tests.

# P27 — Multimodal RAG Real Ingestion and Visual Evidence Loop

**Status:** `P27_BLOCKED`

**Date:** 2026-10-17 (Asia/Shanghai)

**Repository:** `E:\RAG-Worktrees\weknora-python-port-20261013`

**Branch / starting HEAD:** `codex/weknora-python-port-integrate-image-20261014` / `78d277b12ed6ea5eb82a61a73160ef41a5c7027f`

## Outcome

The existing image-processing and retrieval code has a normal ingestion path, and its focused offline tests pass. The requested real visual-evidence-to-RAG loop could not be executed safely in this round: no new finite P27 external-call fence was established; the active configuration recorded in P26 has multimodal processing disabled and no configured VLM; the product Python environment is missing the pinned optional PDF parser dependency; and the current ingestion/task contract has no page selector. No service, upload, worker task, database write, embedding, rerank, vision request, or Cheap answer was started. No Gold content was added to any request or index.

`P27_BLOCKED` is used because neither target completed a real visual evidence loop. P27 is limited to an offline audit and regression check, not a partial real RAG acceptance.

## P24–P26 state and ownership

The inherited dirty files were preserved and not staged by the P27 work:

- Modified: `.serena/project.yml`, `progress.md`.
- Untracked: `docs/weknora-port/AUTHORITY_STOP_STATE_20261015.json`, `docs/weknora-port/OWNER_DEEPSEEK_AUTHORITY_HANDOFF_20261015.md`, `docs/weknora-port/OWNER_DEEPSEEK_HANDOFF_20261014.md`, and the three P6 ordinary Go build evidence files under `docs/weknora-port/evidence/`.
- There were no staged changes at the start of the P27 report work.

P24–P26 were already organized into separate local commits before P27 files were added:

| Phase | Commit | Scope |
|---|---|---|
| P24 | `cb07fb50874386ade91413e059f096e328c0e47b` | P24 report and result JSON only |
| P25 | `5f949a97bd31b71b33cf51d2e9a2af3253261abc` | P25 report and result JSON only |
| P26 | `78d277b12ed6ea5eb82a61a73160ef41a5c7027f` | Narrow public source-handle fix, tests, and P26 report/results |

The P25 token fence remains historical/closed. This run did not reopen, extend, reset, or consume it. No historical `UNKNOWN`, Gold, result, or index was changed.

## Existing implementation audit

The fixed upstream reference remains WeKnora commit `3e8b0bfc80b845b2d4b2ed683994748741450a97`; no newer upstream revision was used for this audit.

| Stage | Existing implementation reviewed | P27 finding |
|---|---|---|
| Configuration selection | `backend/weknora_port/image_configuration.py`: `effective_image_config`, `_vision_binding`, `resolve_image_selection` | KB defaults and non-empty upload overrides are merged; OCR and Caption can select separate adapters/models, and Caption can follow a validated VLM OCR selection. This reuses the model store/catalogue and existing vision declarations; it does not infer capability from a model name or probe a provider. |
| PDF asset extraction | `backend/weknora_port/ingestion/upstream_pdf_parser.py`: `PDFParser.parse_into_text`, `_route_locked` | Per-page parser emits scanned-page assets and can emit embedded/vector image assets. It emits no target chart asset for the Pew PDF under the tested default configuration. |
| Durable task and image processing | `backend/weknora_port/documents.py`: `upload_document`, `_process_document`, `_persist_assets`; `backend/weknora_port/image_tasks.py`; `backend/weknora_port/worker.py` | Upload can enqueue the image task when KB multimodal processing is enabled. The worker processes the saved task/config snapshot. `DocumentService` sends all pending image assets to the processor; there is no physical-page filter in the upload override or task snapshot. |
| OCR/Caption result persistence | `backend/weknora_port/image_multimodal.py`: `SourceImageProcessor.process_plan` | OCR and Caption are processed separately per asset when both use a model. Results retain page/source metadata, separate OCR/Caption status and errors, and become `image_ocr`/`image_caption` child chunks linked to the source parent/version. Empty output is rejected rather than stored as success. |
| Embedding and retrieval | `backend/weknora_port/documents.py` (`HybridIndexer.batch_index`); `backend/weknora_port/retrieval/rerank.py`; `backend/weknora_port/retrieval/context.py` | Persisted derived children use the existing text indexing path. Rerank and Context can enrich passages with persisted `caption` and `ocr_text`. This code path was not exercised against a database in P27. |
| Final model request | `backend/weknora_port/generation/context.py` | Final generation can include directly attached images when supported; retrieved image bytes are not forwarded as images through this path. For this task, the intended RAG route therefore depends on persisted OCR/Caption text entering the final Context. |

The task queue is image-specific and preserves the fixed upstream retry/claim semantics already documented in its source; the memory-only extraction queue was not reused. The existing P26 tests cover offline configuration and image processing, but do not prove live model quality or the full upload-to-answer path.

## Frozen source and parser preflight

The frozen source identities matched the P10/P24 manifests. The inventory evidence is `E:\WK\run\p27-20261017\offline_parser_asset_inventory.json`; it contains asset paths/page identities and counts, not image bytes.

| QID | Source SHA-256 | Target page | Offline parser output | P27 disposition |
|---|---|---:|---|---|
| `row-0684` | `8f49f93f1665fd5ff604e66ffed9649d8b18ce7f114ab43463aeac46cd3d00cb` | 10 | 45 text pages, 0 scanned pages, 0 embedded images, 0 vector figures, 0 assets. The chart page has no asset for OCR/Caption to process. | `NOT_RUN` — chart-specific 81%/78% evidence cannot be generated through the current default parser output. |
| `row-0958` | `43be9592854c7da10bf011335faec017b726ff2e23bb4ccae7e8fe88d7801f22` | 9 | 21 scanned pages and 21 full-page image assets; physical page 9 maps to `images/b3m5kaeqm2w8n4bwcesw-140602121350-phpapp02_95_page_9.jpg`. | `NOT_RUN` — without page filtering, a normal upload sends all 21 assets to image processing. With Tesseract OCR plus separate VLM Caption that is 21 VLM calls; with VLM for both OCR and Caption it is 42. These are code-path counts, not executed calls. Both exceed P27's four-VLM-call ceiling. |

**Runtime qualification:** the asset inventory ran the repository parser code under `E:\WK\mineru-env\Scripts\python.exe` (Python 3.12.7, `pypdfium2` 5.14.0) only as an offline feasibility preflight. The product runtime `E:\RAG quention\.venv\Scripts\python.exe` is Python 3.13.0 and reports `pypdfium2_spec=None`; the declared optional extra pins `pypdfium2==5.8.0`. Therefore the preflight is not a successful parser run in the product interpreter and does not prove API/Worker parsing will work there. The alternate interpreter also emitted PDFium bitmap-close warnings after parsing.

## Active capability and authorization blockers

P26's saved configuration audit found that the active KB image settings had `enable_multimodal=false` and empty `vlm_config`; no VLM role was configured. The selected Cheap model was not declared as a vision model (`supports_vision=false`). P27 did not infer vision capability from its name or chat support and did not make a capability probe. No existing local OCR result was promoted to evidence for the current index.

P27 requires a newly approved finite external-call fence. No new P27 fence/amount was present or established in this execution. The prior P25 fence is closed and was not reused. Even with a fence, P27 would still need an authorized, configured image-capable model/adapter for the visual semantics and a bounded way to process only the target page(s) through the normal upload/task path.

## Acceptance results

| Acceptance item | Status | Evidence |
|---|---|---|
| Image extraction | `PARTIAL / OFFLINE ONLY` | Source hashes and page-to-asset mapping in the saved inventory JSON; B3 p9 asset exists in alternate parser runtime; Pew p10 asset does not. |
| OCR/VLM | `NOT_RUN` | VLM requests: 0. No OCR response or Caption was generated in P27. |
| Persistence | `NOT_RUN` | Uploads: 0; image tasks: 0; OCR/Caption child chunks: 0. |
| Vector retrieval | `NOT_RUN` | Embeddings/search/rerank requests: 0; no PG/Redis mutation or query. |
| Context to Cheap | `NOT_RUN` | Cheap final answers: 0; no model Context was constructed or sent. |
| Answer/Gold comparison | `NOT_APPLICABLE` | No answer was generated. Gold remained evaluation-only. |
| Token / latency | `0 new provider tokens`; latency `N/A` | Provider requests: 0; new `UNKNOWN`: 0. |
| Focused offline regression | `PASS (offline/mock tests only)` | 76 tests passed; output in `E:\WK\run\p27-20261017\image_offline_pytest.log`. |

No claim is made that P27 validated real HTTP, Worker, PostgreSQL/pgvector, Redis, indexing, or model quality.

## Commands and exit codes

| Command | Exit | Result |
|---|---:|---|
| `& 'E:\WK\mineru-env\Scripts\python.exe' 'E:\WK\run\p27-20261017\offline_parser_asset_inventory.py'` | 0 | Offline parser feasibility inventory; PDFium bitmap-close warnings were emitted. Saved JSON listed above. Alternate Python/dependency versions apply. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest tests/weknora_port/test_image_configuration.py tests/weknora_port/test_image_multimodal.py tests/weknora_port/test_image_processing.py tests/weknora_port/test_image_json_boundary.py tests/weknora_port/test_search_image_completion.py -q` | 0 | `76 passed in 1.25s`; offline/unit coverage only. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -c "import sys, importlib.util; print(sys.version.split()[0]); print('pypdfium2_spec=', importlib.util.find_spec('pypdfium2'))"` | 0 | `3.13.0`; `pypdfium2_spec=None`. |
| Real upload / API / Worker / PG / Redis / VLM / Cheap RAG | `NOT RUN` | Blocked before execution by missing fresh finite P27 fence, missing active VLM configuration, the product-runtime parser dependency gap, and lack of bounded page selection. No services were started. |

## Minimal next step

Before resuming real requests, establish a new finite P27 token fence through the trusted authority, select and validate an image-capable adapter/model without a probe request, and make the product interpreter's already-declared parser extra available through the normal environment process. Then add or use an approved page-scoped normal-ingestion mechanism so B3 does not exceed the four-VLM ceiling. Pew requires a parser-supported figure extraction or other normally sourced image asset for physical page 10. Do not copy manual descriptions, use Gold as ingestion data, or bypass task persistence/indexing.

No production code was changed by P27. Only this report, its result JSON, and external run evidence were added. No push, deployment, merge, tag, or P28 work was performed.

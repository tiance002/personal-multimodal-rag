# P27 — Multimodal RAG Real Ingestion and Visual Evidence Loop

**Status:** `P27_BLOCKED`

**Date:** 2026-10-17 (Asia/Shanghai)

**Repository:** `E:\RAG-Worktrees\weknora-python-port-20261013`

**Branch / starting HEAD:** `codex/weknora-python-port-integrate-image-20261014` / `78d277b12ed6ea5eb82a61a73160ef41a5c7027f`

## Outcome

The P27 continuation completed the bounded offline wiring: the product virtual environment now has the already-pinned `pypdfium2==5.8.0`; DeepSeek V4.1 Flash resolves through the existing model catalogue as image-capable; the normal PDF upload override and worker snapshot carry a one-based physical-page scope bound to document identity and SHA-256; and both frozen target pages produce a correctly linked image asset in the product interpreter. The local Tesseract path also recognized a synthetic test image using the existing tessdata directory.

The owner explicitly authorized a new maximum fence of 1,000,000 tokens and selected `deepseek-flash` for ingestion-time Caption. No provider calls were sent: the repository's trusted approved authority still reports `APPROVED_METER_UNIMPLEMENTED` and does not verify production source facts, historical UNKNOWN upper bounds, or shared-account carry-in. The last recorded P26 knowledge-base configuration was also multimodal-disabled with no VLM role, and no new database/model configuration was applied. No upload, worker task, PostgreSQL/Redis write, embedding, rerank, DeepSeek image request, or Cheap answer was started. No Gold content was added to a request, parser input, or index.

`P27_BLOCKED` remains the accurate result because neither target completed the real visual-evidence-to-RAG loop. This continuation is an offline implementation and parser-readiness result only, not a partial real RAG acceptance.

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
| Durable task and image processing | `backend/weknora_port/documents.py`: `upload_document`, `_process_document`, `_persist_assets`; `backend/weknora_port/image_tasks.py`; `backend/weknora_port/worker.py` | P27 continuation adds `image_pages` as a one-based physical-page selection. Upload configuration wins over KB defaults; null does not clear a valid KB default. The durable `selected_config` snapshot stores selected pages plus tenant, subject, KB, document, source version, and source SHA-256. The worker validates the snapshot and current document hash before image requests, then filters pending image assets to selected pages. |
| OCR/Caption result persistence | `backend/weknora_port/image_multimodal.py`: `SourceImageProcessor.process_plan` | OCR and Caption are processed separately per asset when both use a model. Results retain page/source metadata, separate OCR/Caption status and errors, and become `image_ocr`/`image_caption` child chunks linked to the source parent/version. Empty output is rejected rather than stored as success. |
| Embedding and retrieval | `backend/weknora_port/documents.py` (`HybridIndexer.batch_index`); `backend/weknora_port/retrieval/rerank.py`; `backend/weknora_port/retrieval/context.py` | Persisted derived children use the existing text indexing path. Rerank and Context can enrich passages with persisted `caption` and `ocr_text`. This code path was not exercised against a database in P27. |
| Final model request | `backend/weknora_port/generation/context.py` | Final generation can include directly attached images when supported; retrieved image bytes are not forwarded as images through this path. For this task, the intended RAG route therefore depends on persisted OCR/Caption text entering the final Context. |

The task queue is image-specific and preserves the fixed upstream retry/claim semantics already documented in its source; the memory-only extraction queue was not reused. The existing P26 tests cover offline configuration and image processing, but do not prove live model quality or the full upload-to-answer path.

## Frozen source and parser preflight

The frozen source identities matched the P10/P24 manifests. The inventory evidence is `E:\WK\run\p27-20261017\offline_parser_asset_inventory.json`; it contains asset paths/page identities and counts, not image bytes.

| QID | Source SHA-256 | Target page | Offline parser output | P27 disposition |
|---|---|---:|---|---|
| `row-0684` | `8f49f93f1665fd5ff604e66ffed9649d8b18ce7f114ab43463aeac46cd3d00cb` | 10 | Default parsing: 45 text pages, no image assets. P27 page-scoped parsing adds exactly one native-page render for page 10 while retaining the PDF text. | `NOT_RUN` — offline target image is ready; real Caption/RAG is blocked by the trusted authority. |
| `row-0958` | `43be9592854c7da10bf011335faec017b726ff2e23bb4ccae7e8fe88d7801f22` | 9 | 21 scanned pages and 21 full-page image assets; physical page 9 maps to `images/b3m5kaeqm2w8n4bwcesw-140602121350-phpapp02_95_page_9.jpg`. P27 page filter selects only page 9 for image processing. | `NOT_RUN` — target image is ready and its parent reference is fixed; real Caption/RAG is blocked by the trusted authority. |

**Initial runtime qualification:** the first inventory used `E:\WK\mineru-env\Scripts\python.exe` (Python 3.12.7, `pypdfium2` 5.14.0). At that point the product Python 3.13.0 environment lacked its already-declared optional `pypdfium2==5.8.0`. In this continuation, the exact pinned wheel was installed into the project virtual environment only; the product interpreter then parsed both frozen PDFs and created the selected page assets. No global Python installation, package pin, or lock file was changed. The initial alternate-interpreter inventory's PDFium bitmap-close warnings remain historical evidence; the product-interpreter targeted run completed without that warning.

## P27 continuation: offline wiring and frozen-page proof

The configured product-runtime dependency is the existing project pin `pypdfium2==5.8.0`. It is installed only under `E:\RAG quention\.venv`; parser tests run with that interpreter. The focused full-source `test_ingest.py` module could not be collected because the same environment lacks the separately declared optional `python-pptx==1.0.2` import (`ModuleNotFoundError: No module named 'pptx'`). A dedicated parser test module without Office imports covers the requested PDF behavior and passed; the missing `python-pptx` dependency was not installed because it is outside this task's parser runtime fix.

DeepSeek model resolution was checked offline through `SourceCatalogue` for a `vlm` role with provider `deepseek` and API model ID `deepseek-flash`. The pinned registry declares `input: ["text", "image"]`; the resolved config has `supports_vision=true`, OpenAI-compatible chat, and multi-content support. The request renderer emits the configured prompt and `data:image/jpeg;base64,...` image as separate user-content parts. The provider's vision path is explicit and does not silently retry after stripping the image. The repository's catalog/request contract and adapter tests passed. This is a configuration and wire-shape check, not a live provider verification. DeepSeek's official [Vision guide](https://api-docs.deepseek.com/guides/vision/) documents image_url content on the chat-completions API, and the [V4.1 Flash release](https://api-docs.deepseek.com/news/news260910/) identifies the API model ID.

Local OCR was checked using the product interpreter, existing `E:\WK\deps\tessdata`, and a generated image containing `LOCAL OCR PAGE 9`. `TesseractOCR.recognize` returned that text with `image-ocr-layout-v1`; this is a local synthetic smoke only, not OCR evidence for either benchmark PDF. The frozen dataset's actual PDF page content was not sent to OCR or a provider.

The upload/worker page selector accepts one-based physical pages (`image_pages`, 1–1000), validates positive unique integers, applies upload override before KB default, preserves the full original PDF, and passes selected pages to the existing PDF parser. Native text pages are rendered only when selected; existing scanned-page asset identities are retained. Only selected page assets are sent to the image processor. The page list and source identity are stored in the task's existing `selected_config` JSON snapshot; no schema migration or new model registry was added. Worker source version and file hash are checked before processing. A parser path mismatch for requested scanned pages was fixed: Markdown now references the existing `_page_N.jpg` asset rather than a nonexistent `_requested_render.jpg` asset.

Using the frozen source manifest and verifying both source SHA-256 values, the product Python 3.13.0 parser produced:

| Sample | Requested physical page | Full-document assets emitted by parser | Selected page asset | Parent Markdown reference | Offline result |
|---|---:|---:|---|---|---|
| `row-0958` B3 PDF | 9 | 21 scanned-page assets | `images/b3m5kaeqm2w8n4bwcesw-140602121350-phpapp02_95_page_9.jpg` | Present; scanned asset identity preserved | `PASS / parser only` |
| `row-0684` Pew PDF | 10 | 1 selected native-page render | `images/11-21-16-Updated-Post-Election-Release_p10_requested_render.jpg` | Present | `PASS / parser only` |

The B3 parser still emits its 21 scanned-page assets for the full document; the normal image-task filter selects only page 9 for VLM processing. The Pew parser retains all native document text and renders page 10 as an additional sourced asset. These are parser/selection results, not persistence, indexing, retrieval, or answer results.

## Active execution blockers

The owner has now authorized a new P27 maximum fence of 1,000,000 tokens and selected DeepSeek V4.1 Flash / `deepseek-flash`; that authorization is recorded and the closed P25 fence was not reused. The trusted production authority still cannot establish an approved send: `docs/weknora-port/AUTHORITY_ASSEMBLY_REPORT_20261015.md` reports `APPROVED_METER_UNIMPLEMENTED`, missing production source-fact assembly, and unverified real carry-in/history-UNKNOWN bounds/shared-account consumers. The system does not permit a real request merely from a requested numeric limit, so the 1,000,000-token cap has not been installed as a trusted live fence and no request was attempted.

P26's last saved configuration audit also recorded `enable_multimodal=false`, empty KB VLM config, and no VLM model role. The continuation did not read secret values or change the live model configuration. Therefore no active DeepSeek model credential/authorization or corresponding Cheap/embedding/rerank config was verified for the isolation KB. No service or database was started or changed.

## Acceptance results

| Acceptance item | Status | Evidence |
|---|---|---|
| Image extraction | `PASS / offline product-runtime parser only` | Verified frozen source hashes; B3 page 9 and Pew page 10 assets and matching Markdown references in `E:\WK\run\p27-resume-20261017\actual-pdf-page-scope.json`. |
| OCR/VLM | `PARTIAL / local smoke; DeepSeek NOT_RUN` | Local synthetic OCR returned `LOCAL OCR PAGE 9`; DeepSeek image requests: 0. No benchmark Caption was generated. |
| Persistence | `NOT_RUN` | Uploads: 0; image tasks: 0; OCR/Caption child chunks: 0. |
| Vector retrieval | `NOT_RUN` | Embeddings/search/rerank requests: 0; no PG/Redis mutation or query. |
| Context to Cheap | `NOT_RUN` | Cheap final answers: 0; no model Context was constructed or sent. |
| Answer/Gold comparison | `NOT_APPLICABLE` | No answer was generated. Gold remained evaluation-only. |
| Token / latency | `0 new provider tokens`; latency `N/A` | Provider requests: 0; new `UNKNOWN`: 0. |
| Focused offline regression | `PASS (offline/mock tests only)` | 492 passed across image config, image processing, JSON boundary, retrieval completion, provider request/transport, model catalog, document lifecycle, HTTP API, and P27 page-scope tests. Log: `E:\WK\run\p27-resume-20261017\focused-suite-final.log`. |

No claim is made that P27 validated real HTTP, Worker, PostgreSQL/pgvector, Redis, indexing, or model quality.

## Commands and exit codes

| Command | Exit | Result |
|---|---:|---|
| `& 'E:\WK\mineru-env\Scripts\python.exe' 'E:\WK\run\p27-20261017\offline_parser_asset_inventory.py'` | 0 | Offline parser feasibility inventory; PDFium bitmap-close warnings were emitted. Saved JSON listed above. Alternate Python/dependency versions apply. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest tests/weknora_port/test_image_configuration.py tests/weknora_port/test_image_multimodal.py tests/weknora_port/test_image_processing.py tests/weknora_port/test_image_json_boundary.py tests/weknora_port/test_search_image_completion.py -q` | 0 | `76 passed in 1.25s`; offline/unit coverage only. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -c "import sys, importlib.util; print(sys.version.split()[0]); print('pypdfium2_spec=', importlib.util.find_spec('pypdfium2'))"` | 0 | `3.13.0`; `pypdfium2_spec=None`. |
| `pip download --no-cache-dir --only-binary=:all: --no-deps --dest E:\WK\run\p27-resume-20261017\wheels pypdfium2==5.8.0` | 0 | Existing pinned Windows wheel downloaded to the E-drive run directory. |
| `pip install --no-index --no-deps --find-links E:\WK\run\p27-resume-20261017\wheels pypdfium2==5.8.0` | 0 | Installed into `E:\RAG quention\.venv`; no global environment or dependency manifest changed. |
| Product interpreter import/version check for `pypdfium2` | 0 | Python `3.13.0`; package metadata `5.8.0`; module loaded from the project `.venv`. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest tests/weknora_port/test_p27_page_scoped_vision.py tests/weknora_port/test_image_configuration.py tests/weknora_port/test_image_multimodal.py tests/weknora_port/test_image_processing.py tests/weknora_port/test_image_json_boundary.py tests/weknora_port/test_search_image_completion.py tests/weknora_port/test_provider_request.py tests/weknora_port/test_provider_transport.py tests/weknora_port/test_catalog.py tests/weknora_port/test_documents.py tests/weknora_port/test_http_api.py -q -p no:cacheprovider --basetemp=E:/RAG-Tests/p27r3` | 0 | `492 passed in 3.61s`; log and exit-code file in `E:\WK\run\p27-resume-20261017\`. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest tests/weknora_port/test_ingest.py -q -p no:cacheprovider --basetemp=E:/RAG-Tests/p27-ingest-blocked` | 2 | Collection blocked by missing `pptx` module. Evidence: `E:\WK\run\p27-resume-20261017\test-ingest-collection-failure.log`; independent P27 parser tests passed in the 491-test suite. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -c "import io; from PIL import Image, ImageDraw, ImageFont; from backend.weknora_port.tesseract_ocr import TesseractOCR; image=Image.new('RGB',(1200,300),'white'); draw=ImageDraw.Draw(image); draw.text((40,90),'LOCAL OCR PAGE 9',fill='black',font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',72)); out=io.BytesIO(); image.save(out,format='PNG'); result=TesseractOCR(r'E:/WK/deps/tessdata').recognize(out.getvalue(),'image/png'); print('error='+result.error); print('text='+repr(result.text)); print('layout='+result.layout.get('schema',''))"` | 0 | Text `LOCAL OCR PAGE 9`, `image-ocr-layout-v1`; log `E:\WK\run\p27-resume-20261017\local-tesseract-smoke.log`. |
| Product-interpreter page-scoped parse for frozen `row-0958` p9 and `row-0684` p10 | 0 | Both original SHA values matched; both target assets and parent Markdown references were found. JSON/log: `E:\WK\run\p27-resume-20261017\actual-pdf-page-scope.json` and `actual-pdf-page-scope-final.log`. |
| Real upload / API / Worker / PG / Redis / DeepSeek / Cheap RAG | `NOT RUN` | Blocked because trusted approved meter/source-fact assembly is unimplemented and no active model/KB configuration was applied. No services were started. |

## Minimal next step

The next blocking work is to complete production approved-meter/source-fact assembly without changing historical UNKNOWN or the owner's 1,000,000-token ceiling, then configure the isolated KB/model roles through the normal authorized API. After those gates, run `row-0958` first and proceed to `row-0684` only if the first stage stays within the call cap. No real model calls, database writes, deployments, pushes, merges, tags, or P28 work were performed here.

P27 continuation code changes are limited to page configuration/task snapshot, PDF page rendering/asset selection, and runtime/API adaptation. The exact changed files are recorded in the result JSON; the final local commit SHA is reported in the delivery message. Retrieval thresholds, routing, Gold, ordinary prompts, supplier set, migration files, and dependency locks were not changed.

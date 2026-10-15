# New Python development entry and package

These are source-development defaults, not a replacement of any running container or accepted production image. No service launch, model request or migration is authorized merely by reading this file.

## Explicit settings

The API and worker require WEKNORA_PORT_DATABASE_URL (postgresql://, never the old RAG_DATABASE_URL fallback), DATABASE_NAME (weknora_*), DATABASE_SYSTEM_ID, REDIS_URL (dedicated DB1–15), REDIS_RUN_ID, REDIS_PREFIX (weknora-python-*), STORAGE_ROOT (absolute, new empty directory or matching ownership marker), SECRET_KEY_FILE (absolute file containing exactly32 binary bytes). Prefix every short name in this paragraph with WEKNORA_PORT_. Neither secret bytes nor database credentials are logged. Known old physical PG identities are denied.

Optional: WEKNORA_PORT_TOKENIZER_ASSET (local hash-pinned cl100k_base file), ROUTER_MODE (DYNAMIC by default; fixed0.35 and escalation switches unchanged), OLLAMA_URL (explicit local service; allow_pull=false), ORIGINS (comma-separated browser origins). No daemon startup/model pull. Missing tokenizer preserves UNKNOWN input load, never zero. The HTML renderer remains the explicit hash-bound WEKNORA_HTML_CODEC_PATH / WEKNORA_HTML_CODEC_SHA256 dependency; missing renderer fails its relevant format path rather than using a different algorithm.

## Startup and tasks

Install with python -m pip install '.[weknora-port]'. Run weknora-python-api and, separately, weknora-python-worker. Python module equivalents are backend.weknora_port.entrypoint and backend.weknora_port.worker. Imports open no connections. Console entrypoints use a SelectorEventLoop for psycopg on Windows.

API: source bytes/upload, ordinary text parsing+indexing inline, session/RAG/SSE/reference and settings routes. Image-bearing uploads persist image tasks and return pending; the new development API does not start a hidden consumer.

Worker: actual ImageTaskQueue and RedisMemoryExtractionQueue consumers. Image task continues parse/OCR/caption/chunk/index/profile ingestion. Memory task uses the bound ExtractionWorker. No legacy backend.app.workers.ingestion polling or placeholder loop. --once runs one due task in each actual queue and reports their outcomes; normal mode runs their existing source-aligned loops. Separate API/worker instances share physically verified resources and local principal; claim fencing/UNKNOWN remain unchanged.

Schema is never migrated by API startup. Only an explicitly approved EMPTY new database can run weknora-python-init --empty-new-database; it verifies physical identity and emptiness before the existing hash-checked001–011 migrations. It refuses an existing populated database. This batch does not invoke that initializer on business data.

## Model authorization boundary

The default assembly supplies no fabricated send authority, carry-in grant or price. P1SendGate therefore rejects all model sends until an approved server-side permission/cost authority is bound. /healthz identifies the local API; /readyz returns503 DENY_UNCONFIGURED in that state. Model configuration/read-only UI is not proof of real-provider readiness. Injected synthetic authorities in isolated tests do not authorize real calls. A reviewed deployment adapter must bind real authorities before any separately authorized provider/Local acceptance run; no env-specified arbitrary Python factory is loaded.

## Compose and rollback

The new candidate project is weknora-python-dev, with separate dev DB/Redis/storage volumes and ports18000/14173. deploy/compose.data.yml describes only new data services so an owner-approved shadow setup can inspect new IDs before configuring API/worker. deploy/compose.yml requires explicit IDs, host key file and tokenizer asset mount. Do not reuse old volumes, old database identities or passwords. All actual docker compose up, container replacement and production configuration remain separately approved actions; none is executed in this batch.

Rollback source with Git; old actual services/images/volumes remain unchanged. Previous source config is under deploy/legacy and previous README under docs/history. Do not point old images at new schema or new code at old DB to simulate rollback. See LEGACY_ENTRYPOINTS.md. P8 old-code deletion is a later exact-file reviewed commit after functional/cutover acceptance.

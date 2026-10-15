# New Python development entry and package

These are source-development defaults, not a replacement of any running container or accepted production image. No service launch, model request or migration is authorized merely by reading this file.

## Explicit settings

The API and worker require WEKNORA_PORT_DATABASE_URL (postgresql://, never the old RAG_DATABASE_URL fallback), DATABASE_NAME (weknora_*), DATABASE_SYSTEM_ID, REDIS_URL (dedicated DB1–15), REDIS_RUN_ID, REDIS_PREFIX (weknora-python-*), STORAGE_ROOT (absolute, new empty directory or matching ownership marker), SECRET_KEY_FILE (absolute file containing exactly32 binary bytes). Prefix every short name in this paragraph with WEKNORA_PORT_. Neither secret bytes nor database credentials are logged. Known old physical PG identities are denied.

Optional: WEKNORA_PORT_TOKENIZER_ASSET (local hash-pinned cl100k_base file), ROUTER_MODE (DYNAMIC by default; fixed0.35 and escalation switches unchanged), OLLAMA_URL (explicit local service; allow_pull=false), ORIGINS (comma-separated browser origins). No daemon startup/model pull. Missing tokenizer preserves UNKNOWN input load, never zero. The HTML renderer remains the explicit hash-bound WEKNORA_HTML_CODEC_PATH / WEKNORA_HTML_CODEC_SHA256 dependency; missing renderer fails its relevant format path rather than using a different algorithm.

## Startup and tasks

Install with python -m pip install '.[weknora-port]'. Run weknora-python-api and, separately, weknora-python-worker. Python module equivalents are backend.weknora_port.entrypoint and backend.weknora_port.worker. Imports open no connections. Console entrypoints use a SelectorEventLoop for psycopg on Windows.

API: source bytes/upload, ordinary text parsing+indexing inline, session/RAG/SSE/reference and settings routes. Image-bearing uploads persist image tasks and return pending; the new development API does not start a hidden consumer.

Worker: actual ImageTaskQueue and RedisMemoryExtractionQueue consumers. Image task continues parse/OCR/caption/chunk/index/profile ingestion. Memory task uses the bound ExtractionWorker. No legacy backend.app.workers.ingestion polling or placeholder loop. --once runs one due task in each actual queue and reports their outcomes; normal mode continuously polls both persisted queues, including jobs arriving after an idle period. Separate API/worker instances share physically verified resources and local principal; claim fencing/UNKNOWN remain unchanged.

Schema is never migrated by API startup. Only an explicitly approved EMPTY new database can run weknora-python-init --empty-new-database; it verifies physical identity and emptiness before the existing hash-checked001–011 migrations. It refuses an existing populated database. This batch does not invoke that initializer on business data.

## Model authorization boundary

The default assembly supplies no fabricated send authority, carry-in grant or price. P1SendGate therefore rejects all model sends until an approved server-side permission/cost authority is bound. /healthz identifies the local API; /readyz returns503 DENY_UNCONFIGURED in that state. Model configuration/read-only UI is not proof of real-provider readiness. Injected synthetic authorities in isolated tests do not authorize real calls. A reviewed deployment adapter must bind real authorities before any separately authorized provider/Local acceptance run; no env-specified arbitrary Python factory is loaded.

## Compose and rollback

The new candidate project is weknora-python-dev, with separate dev DB/Redis/storage volumes and ports18000/14173. deploy/compose.data.yml describes only new data services so an owner-approved shadow setup can inspect new IDs before configuring API/worker. deploy/compose.yml requires explicit IDs, host key file and tokenizer asset mount. Do not reuse old volumes, old database identities or passwords. All actual docker compose up, container replacement and production configuration remain separately approved actions; none is executed in this batch.

Rollback source with Git; old actual services/images/volumes remain unchanged. Previous source config is under deploy/legacy and previous README under docs/history. Do not point old images at new schema or new code at old DB to simulate rollback. See LEGACY_ENTRYPOINTS.md. P8 old-code deletion is a later exact-file reviewed commit after functional/cutover acceptance.

## Local operations — start / stop / containers / volumes / logs / backup / rollback (2026-10-15)

- **Containers (purpose).** Only two shared dependency containers are grouped: Compose project `eviroute-rag` → `eviroute-rag-postgres` (`127.0.0.1:56423`, image `paradedb/paradedb:v0.22.6-pg17`) and `eviroute-rag-redis` (`127.0.0.1:56424`, image `redis:7.4.2`), both `pull_policy: never`. The API and React frontend still run as host processes and are intentionally not containerized. Details: `docs/weknora-port/EVIRoute_RAG_CONTAINERS_20261015.md`.
- **Data volumes (external named; never re-created empty).** `weknora-python-resume-pg-20261013` and `weknora-python-resume-redis-20261013`. The helper script refuses to start if either is missing rather than creating an empty replacement.
- **Start.** Dependencies: `.\scripts\eviroute-rag-dependencies.ps1 start` (checks both volumes first). API/worker (host processes): `weknora-python-api` (module `backend.weknora_port.entrypoint`) and `weknora-python-worker` (module `backend.weknora_port.worker`). Task 1 added an optional `--authority-config <absolute D/E-drive JSON>` to both; `mode=synthetic` shares one authority assembly across API and worker, while `mode=approved` currently raises `APPROVED_METER_UNIMPLEMENTED`, so real sends stay refused and `/readyz` returns 503 `DENY_UNCONFIGURED`.
- **Status / stop.** `.\scripts\eviroute-rag-dependencies.ps1 status`; `… stop` stops only those two Compose services (no `down`, no container/volume deletion). API/worker are ordinary host processes: terminate the process (uvicorn performs a normal shutdown).
- **Logs.** Containers: `docker logs <container>`. API/worker: their host stdout/stderr.
- **Backup.** `scripts/backup.ps1` / `scripts/backup_manifest.py`; the 2026-10-15 migration kept pre-switch PG dump `approved-database.dump` and `redis-before.rdb` (with SHA-256) under `E:\WK\run\browser-accept-20261015\docker-migration\`.
- **Rollback.** Roll source back with Git; old services/images/volumes stay untouched. Never point old images at the new schema or new code at the old databases to simulate a rollback. Unknown/ledger/Gold/original-source evidence must not be released or deleted.

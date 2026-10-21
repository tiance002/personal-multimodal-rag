# Private S8 Linux runtime

This is the formal `backend.weknora_port` API/Worker with server-pinned tenant332.
It creates no model grant and performs no schema initialization, data import or
startup model requests. API and Worker cloud sends are **off**. Only the separately
authorized embedding runner may enable sending within its durable limited grant.
Graph and floorless Top1 are off. Cached MinerU import is handled by the ingestion
tool; this image does not install a second OCR or download large model weights.

## Private files (never in Git/image/build arguments)

Create `/opt/rag-s8/secrets` mode0700 and supply:

- `database_password`: a new DB password, mode0600.
- `model_key`: the original **32 raw bytes** needed for migrated ciphertext, mode0600.
- `api_token`: a random32–256-character token using letters/digits/underscore/hyphen, mode0600.
- `principal.json`: `{ "tenant_id":332, "subject_id":"<verified historical subject>", "subject_sha256":"<actual SHA256>" }`, mode0600.
- `ui.htpasswd`: operator-selected BasicAuth username/password hash. The Nginx worker
  must read this file; mode0644 inside the host's non-traversable0700 secrets directory
  is sufficient. No password/token is embedded in Vite output.

Docker processes initially run as root with capability dropping and
no-new-privileges for API/Worker. This permits reading root-owned0600 bind secrets;
do not change container UID without arranging secret/storage permissions first.
Mount the frozen tokenizer asset at `config/assets/cl100k_base.tiktoken`.
Host `/opt/rag-s8/storage` is mounted at `/var/lib/eviroute/storage`; migrated
`file_path`, asset paths and the new target marker must use this exact container root.

## Explicit startup order

Commands run from the frozen source release root; compose project is always rag-s8.
These are reproduction instructions, not proof that deployment was run locally.

```sh
docker compose --env-file /opt/rag-s8/config/runtime.env -f deploy/s8/compose.yml up -d db redis
# Observe actual new PG system_identifier and Redis run_id, then set those
# non-secret IDs in runtime.env. Placeholder0 is deliberately refused by API.
docker compose --env-file /opt/rag-s8/config/runtime.env -f deploy/s8/compose.yml build api frontend
# Root-owned migration tool initializes only the new DB and copies scoped data,
# Linux source locations and a target-identity storage marker. No DDL at API startup.
docker compose --env-file /opt/rag-s8/config/runtime.env -f deploy/s8/compose.yml up -d api frontend
# Inspect scoped image/memory tasks first: never replay imported old queues.
docker compose --env-file /opt/rag-s8/config/runtime.env -f deploy/s8/compose.yml --profile worker up -d worker
docker compose --env-file /opt/rag-s8/config/runtime.env -f deploy/s8/compose.yml ps
```

PG15432, Redis16379, API18800 and UI18880 publish **127.0.0.1 only**. Use an SSH
tunnel to UI18880, then BasicAuth. Nginx injects the private bearer only when
proxying authenticated same-origin requests. Direct API requests need the bearer,
including `/healthz`, `/readyz`, assets, streams and stream recovery. Token rotation
requires restarting both API and frontend. Startup identity JSON is written to the
private logs volume. `/readyz` checks current verified PG access and Redis run ID.

No old PG16/old Evaluation Center volumes or services are referenced. Never use
`down -v`, prune commands or automatic cleanup. `stop` preserves all data.

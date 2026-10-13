# Actual isolated runtime checkpoint

The Docker engine became available during the Goal; it was not started by this task. Read-only inventory showed the prior RAG API, worker and database stopped. They remain stopped. Only these fresh, restart-disabled fixture instances were created:

| Instance | Image | Container | Host binding | Fresh volume |
|---|---|---|---|---|
| PG | `paradedb/paradedb:v0.22.6-pg17` / `58bf87d2a6f1` | `4394fba805a1` | `127.0.0.1:56413` | `weknora-python-port-pg-20261013` |
| Redis | `redis:7.4.2` / `fbdbaea47b9a` | `8578e63a4ff7` | `127.0.0.1:56414` | `weknora-python-port-redis-20261013` |

PG actual database is `weknora_python_port_20261013`, physical system identifier **7696038373595934758**. The old RAG identifier **7691227493754040358** and old WeKnora identifier **7689510505384681511** were read from `pg_controldata` in disposable, network-disabled, read-only containers with the old volumes mounted read-only. No old server was started. Both identities are prohibited by the new connection policy. Redis actual run ID is **71baa8e1ae7da4e7b8a3de8ecaa642a1c4716bad**; the probe checks it before writing. Prefix is `weknora-python-port:20261013:stream:events`.

Commands, all using Python `E:\RAG quention\.venv\Scripts\python.exe`, with the new fixture DSN only (no environment files or model keys loaded):

```text
scripts/weknora_port_isolated_schema.py --dsn <new fixture DSN> --expected-system-identifier 7696038373595934758 --prohibited-system-identifier 7691227493754040358 --prohibited-system-identifier 7689510505384681511 --report var/weknora-port/isolated-schema.json
scripts/weknora_port_pg_storage_probe.py --dsn <new fixture DSN> --expected-system-identifier 7696038373595934758 --prohibited-system-identifier 7691227493754040358 --prohibited-system-identifier 7689510505384681511 --report var/weknora-port/isolated-retrieval-history.json
scripts/weknora_port_redis_probe.py --dsn <new fixture DSN> --redis-url redis://127.0.0.1:56414/0 --expected-redis-run-id 71baa8e1ae7da4e7b8a3de8ecaa642a1c4716bad --prohibited-system-identifier 7691227493754040358 --prohibited-system-identifier 7689510505384681511 --storage-report var/weknora-port/isolated-retrieval-history.json --report var/weknora-port/isolated-redis-replay.json
```

Each successful command exited **0**. Schema application initially exited **1** due to Windows Psycopg incompatibility with ProactorEventLoop, before database connection; the runner now explicitly uses SelectorEventLoop on Windows and then passed. No schema checksum or previously applied receipt was rewritten to pass.

**PASS — actual isolated PG:** 001 retrieval / 003 common / 002 memory DDL and receipts; pg_search 0.22.6 / vector 0.8.1; HNSW and BM25 retrieval with a known three-dimensional synthetic vector; KB/document isolation; source-body readback; paired question/assistant creation with request idempotency; atomic answer/reference/usage completion; completed-answer overwrite rejection.

**PASS — actual isolated PG + Redis:** append-only events, 24-hour TTL, offset-zero coalesced replay tied to the original assistant/request identity; answer segment completion precedes terminal `complete`; final persistence runs before publishing `complete`. See the recorded raw wire frames and source IDs in `evidence/isolated-redis-replay-20261013.json` and `evidence/isolated-retrieval-history-20261013.json`.

**NOT RUN:** HTTP/browser reconnect against the actual API, complete uploaded-document RAG generation, real provider/real embedding calls, paid calls, old database changes, service replacement, Push/Tag/Release. A successful Redis replay probe is not an HTTP recovery or full Goal acceptance claim. Synthetic answer and vectors are labelled fixtures, not model output or model capability proof.

The new volumes retain their fixture evidence. Do not delete historical records or release UNKNOWN to prepare later tests. Subsequent probes must use fresh requests and distinguish previously stored terminal events from a new generation.

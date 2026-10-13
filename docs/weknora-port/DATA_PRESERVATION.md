# Old data preservation and new resource boundaries

No old source/data deletion, schema/data mutation, model invocation, configuration/key read, service start/replacement or UNKNOWN release in this Goal.

## Existing resources (historical receipts, not new live observations)

The owner-approved old handoff and live report are preserved at `docs/audits/weknora-ordinary-output-progress/{PAUSE_REVIEW_HANDOFF_20261013,LIVE_ACCEPTANCE_20261013}.md`.

- Old Compose project `raglocalfirst0930`; original DB container ID prefix5c9a6eacb994; PostgreSQL database/user `rag`; migration0024.
- Persistent `raglocalfirst0930_rag-db`, `raglocalfirst0930_rag-storage`, Redis storage; original bytes, document/index versions and all references stay in place.
- Historical deployed API/Worker image `sha256:a94a1696a6e41bd7b15b502607d4d9e605428b29ee26fb8914b2f9fdf3dcb82c`, backend sourcece3398a; current HEAD55372f7 is an unvalidated source checkpoint, not that runtime.
- Historical config revision `bcd271c7-e820-45d4-9910-039384d268cd`; no credential values copied or loaded into the new worktree.
- Old successful original/short Runs and failed90/120second long Runs remain immutable. UNKNOWN53915 and known43321, full V4 bound189408/350000 are historical reported values, not a new ledger query. Latest NEW_UNKNOWN_STOP remains effective. No acknowledged request is reinterpreted as free or unsent.
- Old grant/journal files, output probe receipts, backups and private corpus are in ignored original-workspace var/storage; do not copy private data, reset history or use them as a fresh authorization.
- Existing eval_center, six public dataset adapters/Gold, frozen router tests and result artifacts remain tracked and accessible in the new worktree. No full dataset import/rebuild at P0.

## Current inspection limit

Docker Desktop pipe unavailable, read-only dockerps exit1. Therefore live container/DB/storage/Redis/ledger inspection NOT RUN. This is not proof old resources disappeared. Do not start/recreate old services to unblock this task. Reinspect only when the owner environment becomes available; safe isolated implementation continues.

## New environment identity

- Git branch `codex/weknora-python-port`, distinct managed directory, new package `backend/weknora_port`.
- New database `weknora_python_port_20261013`, separate migration location and synthetic fixtures. Neither `rag` nor any old connection string may be an integration write target.
- New Redis prefix `weknora-python-port:20261013:` and dedicated instance/database for integration tests. Do not write old LiveRun keys.
- New synthetic originals under this worktree's ignored `var/weknora-port/storage`; never mount old rag-storage read/write.
- P2 integration runner must check database name, explicit instance identity, isolated volume/root and namespace before DDL/DML. Reject ambiguous identities rather than infer safety from a port number.
- New Chat/Memory records are synthetic; no automatic copy/injection of old GLOBAL/SCOPED records or failed provider text.
- Production migration/cutover remains P8 AWAITING_OWNER_APPROVAL, with separate preservation/readback/rollback proof. No clear/drop/down migration or historical-cost cleanup is authorized.

# P5 generation lifecycle storage

Status: **IMPLEMENTED; 25 scripted SQL-boundary cases PASS; 3 actual isolated PostgreSQL cases PASS**. The three actual cases use psycopg against the approved new database, not a SQL double. Model/provider/budget calls: **0**. 未实现的命令不得报告通过。

## Actual protocol and modified files

At task start `generation/lifecycle.py` did not exist. The implemented contract is the real `generation/types.py::AttemptLifecycle`, used by `GenerationCoordinator`: `claim_run(run_id,payload_sha256,context)`, `begin_attempt(record,context)`, `finish_attempt(record,context)`. No missing lifecycle module was invented.

- `storage/generation.py`: `PostgresGenerationLifecycle`, bound by trusted constructor tenant/session/assistant-message values to an already verified `PostgresRetriever` connection.
- `storage/migrations/004_generation.sql`: local approved P5 lifecycle persistence, not an upstream WeKnora table copy; run/attempt identity, owner metadata, uniqueness/ordinal constraints and UNKNOWN fields.
- `storage/migrations/source_manifest_generation.json`: generated SQL checksum and actual protocol/coordinator source-file hashes.
- `storage/generation_migrate.py`: explicit checksum-verified migration, physical identity reverified before DDL, advisory lock and migration-version record. Root's migration runner was not changed.
- `tests/weknora_port/test_generation_storage.py`: 25 scripted SQL-boundary cases and one opt-in actual PostgreSQL case.
- `tests/weknora_port/test_memory_storage_pg.py`: two additional opt-in real P4 PostgreSQL cases using SelectorEventLoop, no model.
- This report and [P5_GENERATION_STORAGE_EVIDENCE.json](P5_GENERATION_STORAGE_EVIDENCE.json).

Worktree is `C:\Users\22088\.codex\worktrees\weknora-python-port\RAG quention`, branch `codex/weknora-python-port`. Other workers' files were preserved; this worker has not committed or tagged.

## Safety and idempotency

The constructor requires server-resolved `tenant_id`, `session_id`, `message_id`. CallContext must carry the same session and trusted owner. The adapter checks and locks the actual matching `sessions` row and owned assistant `messages` row before claiming or changing attempts. Run rows bind payload SHA and immutable KB/document scope snapshots; mismatched identity/payload/scope conflicts rather than attaching to another user's run. A global run-ID or same-message collision cannot create another invocation. Completed assistant messages cannot be newly claimed.

Same-payload duplicate claim raises `GenerationReplay`, preventing the coordinator from entering provider/Gate calls. Duplicate begin also raises replay rather than returning success and permitting another call. Run locks serialize ordinal checks: only ordinals1/2 and unique attempt IDs are allowed. Second attempt requires finished Cheap followed by Expensive with a recorded allowed objective or `MODEL_CALL_TIMEOUT`; cancelled, budget-denied/general failures and Cheap retry are rejected. Prompt/context SHA must remain equal across attempts. This persists and guards the existing coordinator policy; score/threshold0.35 and routing features are unchanged.

Provider, requested model, endpoint and envelope/prompt/context hashes are immutable after start. Finish preserves the supplied reported model; absent reported identity remains SQL NULL and is never copied from requested identity. Usage JSON retains null counts and UNKNOWN status rather than filling zero or deriving totals. Cost status remains UNKNOWN unless explicitly supplied; storage does not infer settlement. Terminal repeat is persistence-only idempotency; conflicting terminal records cannot overwrite prior evidence. No reservation/settlement method, budget adapter or budget-table write exists in this module.

`generation_runs` uniquely binds tenant/owner/session/message; `generation_attempts` has ordinal1/2 check, unique(run,ordinal) and composite FK(run,tenant,owner). Final answer authority remains root's atomic final-message commit, not this lifecycle table. This task does not modify SSE replay or select another answer.

## Migration and actual PostgreSQL identity

Root had already applied 001→003→002. This worker explicitly applied **004_generation** via `apply_generation_schema(identity_guard)` to:

- Database `weknora_python_port_20261013`, host `127.0.0.1`, port56413.
- **Actual verified system_identifier `7696038373595934758`**.
- Prohibited old system identifiers: `7691227493754040358` and `7689510505384681511`.
- Applied SHA256: `b1eb58dfdbef98a51e3c388f12d0e3d6481604cc82812ef4cb1fff98bf091144`.

The recorded migration version/hash was reread from actual `port_schema_migrations`. Each actual test opens a psycopg mapping-row connection, verifies physical identity before any write, and uses `asyncio.Runner(loop_factory=asyncio.SelectorEventLoop)` on Windows. DSN is supplied via `WEKNORA_PORT_TEST_DSN`; credential text is not stored in the report or evidence. Tests refuse a DSN outside the approved host/port/database, and policy rejects both old system IDs. No old database connection or DML occurred.

## Commands and evidence

Working directory for these commands is the isolated worktree above.

| Exact command | Exit | Result |
|---|---:|---|
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_generation_storage.py -q --tb=short` | 0 | Initial scripted boundary run: **25 passed in 0.30s**, before real-PG case addition |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_generation_storage.py tests/weknora_port/test_memory_storage_pg.py -q --tb=short` | 0 | With explicit approved DSN: final evidence run **28 passed in 1.06s = 25 SQL doubles + 3 actual PostgreSQL**; no skips |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m compileall -q backend/weknora_port/storage/generation.py backend/weknora_port/storage/generation_migrate.py` | 0 | No diagnostics |

The first real run also passed28 in1.07s; its fixture rows were cleaned. The final real run saved assertions and retained the synthetic generation tenant/session/message/run/two-attempt rows for owner-requested UNKNOWN readback. Exact IDs, migration record and actual attempt rows appear in the JSON evidence. P4 memory fixtures were captured before scoped cleanup. All retained records are **new synthetic tests without model calls**, not historical provider usage or real cost evidence.

Actual PostgreSQL coverage:

1. Claim/replay, duplicate begin denial, exactly two unique attempts, typed model-timeout upgrade, third-attempt denial, terminal repeat, foreign-owner denial and readback of NULL reported model/UNKNOWN costs and partial UNKNOWN usage. It constructs no model or budget adapter.
2. P4 scoped CRUD, pending replacement preserving active until confirmation, actual halfvec recall of synthetic vector, edit invalidation, stale-vector rejection, delete, stale cache-revision CAS refusal and empty recall after clear. Deleted synthetic private text cannot reappear via delayed cache write.
3. P4 source-shaped queue/revision/lease/failure-count flow and actual PG delivery queue: coalesced scheduling, busy lease, arrivals retaining pending, third recorded invalid-output skip, final drain, queued duplicate coalescing and running-task successor delivery.

Scripted cases additionally exercise old/unverified identity with no DML, parameterized scope, owner/message denial, payload conflicts, STARTED usage preservation, rollback ordering, immutable request/report identity and cancellation/budget/general-error upgrade exclusions. They remain labelled SQL doubles even though run in the same pytest invocation.

## Limits and next step

These actual checks establish the listed SQL execution and state readbacks in the isolated instance. They do not establish full multi-connection crash/concurrency recovery, real model identity, provider usage/price or successful budget settlement. Those are **NOT RUN** by this worker. Generation rows deliberately retain UNKNOWN; they do not authorize new paid calls. Root still wires lifecycle to the API/coordinator and atomic final commit.

All current assigned files are ready for root review/staging/commit. No acceptance/tag is claimed and no commit was made by this worker.

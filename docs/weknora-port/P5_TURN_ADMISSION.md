# P5 durable ordinary turn admission

## Goal and implementation

The actual `service.py::TurnRepository` now has a PostgreSQL adapter in
`storage/turns.py`. `LocalPrincipalStore` in `storage/local_principal.py` persists
one local tenant/subject binding. Neither adapter implements login, registration,
RBAC, provider access, nor an outbound permission grant. The parent owns runtime
wiring, service, history and the common migration runner.

`accept(request, principal)` validates the constructor-bound principal and locks
the active owned session before resolving its scope. A session's stored KB list
(or its source scalar KB fallback) is the ceiling; caller selections may narrow
it. A stored document list is also a ceiling. With an empty document ceiling,
explicit document selection is accepted only after tenant/KB ownership and
non-deletion checks. Empty KB scope denies admission. It never guesses all KBs.
The resulting sorted scope and exact original question participate in a versioned
payload hash. `q0` stays private in storage and internal request objects; it is
excluded from `ScopeSnapshot.public()` and object reprs. No question is logged.

The scoped primary key is `(tenant_id, subject_id, session_id, client_request_id)`.
The caller ID is at most 36 characters, matching applied source VARCHAR(36).
The first transaction generates a UUID server `run_id` and stable user and
assistant message UUIDs, inserts the two source messages and durable admission
row together, and only then returns `is_new=True`. The server run UUID is both
`AcceptedTurn.request.request_id` and `CallContext.request_id`. Client IDs remain
admission keys; the API must preserve that distinction. A repeated, concurrent,
or restarted admission returns identical IDs and `replay_only=True`. Different
questions or resolved scopes for the same key are rejected. Source-existing
complete message pairs can be adopted as replay without starting a new run.
No adapter method invokes P3, a provider or a budget reserve/settle operation.
Only ordinary `quick` mode is admitted; SMART is explicitly rejected here, with
its separate entry retained by the parent.

`validate_run` checks active owner/session/messages/resources and current session
scope against the frozen snapshot, then returns replay only. It cannot restart
generation. Each operation obtains its own identity-verified connection lease.
The singleton bootstrap uses a transaction advisory lock and never selects an
arbitrary existing tenant. A deleted singleton tenant is an error, not a reset.

`commit_failure` returns an explicit boolean receipt. It atomically writes only
assistant error metadata and admission status with a fixed safe uppercase code;
the user message is unchanged. `True` means failure is durably stored (including
an identical repeat). `False` means the authoritative assistant is already
completed and the service must not publish a failure terminal. Conflicting
failure codes are rejected. This callback never releases UNKNOWN occupancy.

`commit_final` is an adapter callback around the parent-owned
`PostgresHistoryRepository.commit_final`, executed inside the same outer
transaction as admission completion. No history algorithm is copied. Failure
already committed rejects a late final. Repeated identical final commits are
accepted by the source history callback. `status` treats source
`messages.is_completed` as the success authority even if admission metadata is
stale. The added `messages.turn_status` column is failure metadata; completed
answers use `is_completed`, so its default `pending` does not override success.

## Migration and physical boundary

`007_turns.sql` adds singleton and admission wrapper tables plus assistant error
metadata. Its manifest explicitly records these as approved local privacy and
idempotency wrappers, not copied upstream business tables. Existing source
VARCHAR widths and source algorithms are unchanged. Apply after 001 and 003;
this test database already had 002, 004 and 005.

`storage/turns_migrate.py::apply_turn_schema(identity_guard)` independently
verifies the actual physical target, checks the manifest SHA, takes migration
lock 7, and records/rechecks its immutable migration checksum. It does not edit
the parent's migration runner.

- Database: `weknora_python_port_20261013`, localhost port `56413`.
- Actual verified system identifier: `7696038373595934758`.
- Prohibited old identifiers: `7691227493754040358`, `7689510505384681511`.
- Applied 007 SHA256: `ff212d6db2cd21a8823019d47aae4b34aad3b6a2b6df7f39ed3286c7ccf7bf3d`.
- Real tests require an explicit `WEKNORA_PORT_TEST_DSN`, reject a different
  hostname/port/database before connecting, then verify the physical identity
  before writes. Credentials are excluded from this report and evidence JSON.

## Commands and evidence

Working directory: `C:\Users\22088\.codex\worktrees\weknora-python-port\RAG quention`.

Initial scripted boundary command:

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_turn_storage.py -q --tb=short
```

Exit **0**, **10 passed in 0.54s**. These are SQL doubles, not PostgreSQL.

Final command, with the explicit approved isolated DSN supplied through the
environment (no credentials recorded):

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_turn_storage.py tests/weknora_port/test_turn_storage_pg.py -q --tb=short
```

Exit **0**, **13 passed in 1.25s**: **10 SQL doubles + 3 actual PostgreSQL tests**.
The real tests apply/recheck 007 and exercise:

1. Stable local bootstrap across new factories, two independently leased
   connections racing one client key (exactly one new admission), restart
   replay, stable message IDs, private payload conflict rejection, and exactly
   two source message rows.
2. Separate sessions with the same client ID obtaining different server run
   UUIDs; scope expansion, deleted document, changed session scope, deleted
   session, and a client ID longer than source width rejected.
3. Idempotent failure receipt, no user mutation, late final rejected after
   failure, identical atomic final repeated, and late failure returning False
   while the committed answer remains authoritative.

`P5_TURN_ADMISSION_EVIDENCE.json` contains actual row readbacks, server/client IDs,
session IDs, migration SHA and assertions. Synthetic fixtures and singleton
remain in the isolated database for review; no cleanup obscures the readbacks.
Failure text `MODEL_USAGE_UNKNOWN` is a synthetic safe-code fixture only: these
tests create no model call or new budget ledger entry. Existing UNKNOWN ledger
evidence from previous tests is neither reset nor released.

## Result, limitations and handoff

**PASS** for the targeted scripted boundary and selected actual isolated PG
storage invariants. **NOT RUN**: live providers, HTTP/frontend integration,
process-crash injection, full Goal acceptance and milestone verification.
The restart test uses a new repository/factory against retained durable rows;
it does not claim a killed-process recovery exercise. Model calls: **0**;
old-database writes: **0**. 未实现的命令不得报告通过。

All files in this ownership scope are ready for the parent's coordinated commit.
No commit, tag or acceptance claim was made by this worker. The next minimal
step is the parent's runtime wiring of the singleton, fresh lease factory and
turn adapter, using `commit_final` as the final persistence callback and honoring
the explicit boolean failure receipt. No required behavior is deferred here.

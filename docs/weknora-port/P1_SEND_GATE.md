# P1 send permission and monthly budget gate

Status: **IMPLEMENTED; 24 scripted SQL cases PASS; 3 actual isolated PG cases PASS**. HTTP/provider/model sends:0. This implements the actual `models.types.SendGate.authorize/finish(PreparedCall)` protocol; it does not load keys or grant paid calls. 未实现的命令不得报告通过。

## Files and authority boundaries

Changed only assigned `storage/send_gate.py`, independent `005_send_gate.sql`, `source_manifest_send_gate.json`, explicit `send_gate_migrate.py`, `tests/weknora_port/test_send_gate.py`, `test_send_gate_pg.py`, this report and `P1_SEND_GATE_EVIDENCE.json`. Root migration runner, common003/configuration006, transport/model code and old runtime were preserved. Root separately fixed the transport's conservative sent predicate following the reported boundary issue.

`P1SendGate(identity_guard, tenant_id=trusted_tenant, permissions=None, costs=None)` defaults closed. It relies on an already verified physical PostgreSQL identity. Server-injected `PermissionResolver.resolve(call)` and `CostAuthority.monthly/estimate/actual` are the only authority sources; there is no request-body-to-grant conversion or default live authorization.

`SendAuthorization` binds request/owner/provider/model/model_id/endpoint/capability/**exact wire SHA** and supplies explicit approval source/expiry, global outbound permission, complete input provenance, readable/cloud-allowed origin KBs and model input/output hard caps. `ContentOrigin` identifies question/history/memory/evidence/image/document/system source hashes and origin KB IDs; unverified or missing origin fails closed. Only verified public static system content can have no KB origin. All origin KBs **and selected retrieval KBs** require read/egress permission. Origin KBs are persisted separately and never widen retrievalScope. An exact wire manifest must cover every private input, including old history/memory from KBs outside current retrieval scope. PreparedCall does not carry raw body, so the server resolver must build/register the complete manifest against the actual serialized wire hash; unknown provenance is a denial, not guessed from current KB.

Each call, batch/fallback identity and upgrade invokes permission/price/monthly checks afresh. Grant expiry is checked again after budget/lock waits. A unique request/call ID can never reopen or resend—even after NOT_SENT release. Configured provider/model/model_id/endpoint and body hash must match the grant; finish cannot change them. Input upper-bound tokens and positive chat output budget must fit the explicitly authorized model limits. The gate does not truncate prompts or change options/routing.

## Budget semantics and carry-in

Readonly semantic reference: `backend/app/application/budget.py`, `_used`, `PostgresBudgetGate.reserve/settle/release/mark_unknown`. Its runtime is never imported. `send_budget_months` and `send_calls` are new isolated tables; old model_calls/grants/UNKNOWN rows are not reset or rewritten.

`MonthlyAuthority` requires configured account, actual UTC month, approved monthly microunit budget, carry-in amount/status/source and approval source. A new empty ledger is **not** evidence of old usage0. Missing carry-in, absent source or UNKNOWN carry-in0 denies admission. Positive UNKNOWN carry-in is an explicit conservative occupancy supplied by the authority; the gate invents no estimate. Once recorded, the month's carry/approval/budget snapshot cannot silently change. Root must supply a documented current-account snapshot or other explicit approved source, including a supported basis for a genuinely new-account/month zero carry.

`ReservationEstimate` requires authoritative price and estimator sources, positive conservative amount (or explicitly confirmed known-zero price) and known positive input-token upper bound. Missing price/count is denied rather than treated free. `ActualCost` uses the reserved price source; known-zero actual settlement requires explicit zero-price authority. No model-name-based free shortcut or token0 substitute is implemented.

Admission takes a PostgreSQL monthly advisory lock keyed by configured account/month. Occupancy is **carry-in + reserved/unknown at reserved amount + settled at actual amount**; released adds0. Comparison and unique call insert commit together. Finish locks the same month before the call record. Only `NOT_SENT` can release a still-reserved call. Possible send, UNKNOWN outcome, missing/incomplete usage or unavailable authoritative actual cost retain UNKNOWN occupancy; UNKNOWN cannot later be released as NOT_SENT. Settling requires RESPONSE_RECEIVED plus fully REPORTED usage and authoritative cost. Null counts stay null.

If actual cost exceeds reservation, the gate commits UNKNOWN with its reservation retained and blocks new monthly admission pending reconciliation, then raises the safe overrun error. This minimal fee-loss guard avoids rolling back the UNKNOWN update or continuing admission against a known broken upper bound; it does not invent a new estimation/routing policy. No automated ledger reset/reconciliation command exists. Terminal finish is immutable and idempotent; a conflicting repeat cannot settle/release again.

## Isolated migration and actual execution

`apply_send_gate_schema(identity_guard)` re-verifies physical identity before DDL, checks manifest SHA, takes migration advisory lock and records version `005_send_gate`. It does not alter006 or root's migration runner.

Actual target: database `weknora_python_port_20261013`, `127.0.0.1:56413`, verified **system_identifier7696038373595934758**. Prohibited old IDs remain7691227493754040358 and7689510505384681511. No old connection/DML occurred. Applied005 SHA256:

`93281549bfc22978f8e47d86aaf6c0e62584ad685a1d72f14e15f9b3bc3df6d1`

Actual tests use psycopg mapping rows and `asyncio.Runner(loop_factory=asyncio.SelectorEventLoop)`. They require explicit `WEKNORA_PORT_TEST_DSN` and reject a host/port/database outside the approved isolated target. Credential text is not stored in reports/evidence. All permissions/prices/carry-in are **synthetic fixture authorities without HTTP sends**, not live paid-call authorization. Synthetic UNKNOWN records are retained for readback.

## Commands and evidence

Working directory: `C:\Users\22088\.codex\worktrees\weknora-python-port\RAG quention`.

| Exact command | Exit | Result |
|---|---:|---|
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_send_gate.py -q --tb=short` | 0 | **24 passed in0.26s**, scripted SQL boundary only |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_send_gate.py tests/weknora_port/test_send_gate_pg.py -q --tb=short` | 0 | With approved explicit DSN, final **27 passed in0.64s = 24 SQL doubles +3 actual PG** (earlier run0.79s also passed) |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m compileall -q backend/weknora_port/storage/send_gate.py backend/weknora_port/storage/send_gate_migrate.py` | 0 | No diagnostics |

The initial scripted run failed one test due to a missing test import (`asdict`); it was corrected and the targeted rerun above passed. That failed run was not counted as a pass.

[P1_SEND_GATE_EVIDENCE.json](P1_SEND_GATE_EVIDENCE.json) records actual identity, migration SHA, account/request IDs and retained ledger rows/assertions. Real cases:

1. Explicit carry10 plus UNKNOWN30, actual-settled12 and reserved30 yields82 used; released call adds0. Another30 is denied against monthly100. Reuse denied; UNKNOWN release denied; partial usage remains null/UNKNOWN.
2. Actual31 over reserved30 commits UNKNOWN, preserves30 occupancy and blocks further admission. No free/reset settlement is performed.
3. Two distinct psycopg connections compete against budget50/carry10 for reservations30 each. Exactly one reservation commits and one returns MONTHLY_BUDGET_EXCEEDED before any send.

Scripted cases cover default/unverified closure, identity/wire mismatch, unknown/private history provenance, output/input limits, missing carry/price/tokens, call-ID reuse, upgrade permission recheck, retrievalScope preservation, terminal immutability and exact reserved/unknown/settled formula. SQL doubles remain explicitly separate from real PG evidence.

## Required runtime integration / limits

Root must wire real server provenance/configured model permissions, approved monthly account/carry-in evidence, pricing/upper-bound estimation and cost receipts to these typed callbacks. Manual memory or historic content without recoverable origin cannot silently be assigned current KB; it must fail closed until provenance/approval is established. Current default callbacks remain absent/deny. This task grants no live paid calls, imports no old grant and reads no provider key.

**NOT RUN:** live HTTP/provider/model usage, real price receipts, actual account carry-in reconciliation, full API Scope/SSE cancellation integration and production paid-call acceptance. The real PG tests establish the listed ledger SQL/transactions and two-connection budget race only. No global Goal acceptance or tag is claimed. Assigned files are ready for root review/commit; worker made no commit.

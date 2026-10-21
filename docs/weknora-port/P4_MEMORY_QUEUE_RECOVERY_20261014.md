# P4 memory queue recovery — bounded implementation evidence

Base: 9a604666f557dbcc3d0940f7c0b5b2964147b76f.
Worktree: E:/WK/mq; branch codex/weknora-memory-queue-20261014.
Owner authorized writes and mock/isolated Redis DB15 checks explicitly in the direct reply.
No main-worktree edits, runtime/extraction/PG CAS changes, merge or push.

## Source mapping and minimal changes
Fixed WeKnora 3e8b0bfc80b845b2d4b2ed683994748741450a97:
- internal/application/service/memory/extract.go enqueueExtraction: ProcessIn and MaxRetry(2).
- internal/router/task.go asynqRetryDelayFunc: memory uses SDK default.
- go.mod pins github.com/hibiken/asynq v0.26.0.
Pinned SDK: recoverer.go recoverLeaseExpiredTasks/retry/archive accounts for expired deliveries;
processor.go markAsDone/retry/archive refuses expired leases and keeps consuming after dequeue errors;
internal/rdb/rdb.go Archive retains task message, failure metadata.
https://raw.githubusercontent.com/hibiken/asynq/v0.26.0/recoverer.go
https://raw.githubusercontent.com/hibiken/asynq/v0.26.0/processor.go
https://raw.githubusercontent.com/hibiken/asynq/v0.26.0/internal/rdb/rdb.go

memory_queue.py CLAIM now atomically consumes retry on expiry, delays with the existing
PRE-increment n^4+15+jitter*(n+1) formula, or archives after max_retry=2.
A fresh delivery_token is persisted on claim; COMPLETE atomically verifies token and
unexpired lease before ACK/retry/archive. Old un-tokened leased jobs remain recoverable.
pump retries only Redis connection/timeout failures, with cancelable .25/.5/1/2/4/5-second
capped backoff; explicit stop cancels it. No provider retry or financial cleanup is added.
Archive retains a fixed whitelist: ID, scoped payload IDs/language, retry, attempts,
failed_at and error_class. No exception messages, credential fields or delivery tokens.
Archive API, UI, retention, Asynq byte protocol/heartbeat/shutdown timing are not implemented.

## Counterexamples and evidence
Identity first failed before writes: identity.* retains that exit1 and 4 identity failures.
Readonly Docker inspection confirmed isolated container /weknora-python-resume-redis-20261013,
volume of the same name, 127.0.0.1:56424 -> 6379, current run_id
586b92f4649b78508382dfd52d64d848f45b0a7c; DB15 initially empty.
All writes use DB15 and unique weknora-python-port:20261013:mq: / mq-regression: prefixes.
Only their exact keys are deleted; no flush, configuration or service mutation.

red.*: original base queue + red_test_source.py, pytest exit1, 4 failures:
late successful ACK deletes successor; late failure rewrites successor retry;
expired delivery bypasses backoff; restored Redis cannot wake a dead pump.
green-precision.*: first fix attempt exit1, 3 passed/1 failed because Redis cjson time
rounding differed by <.001 seconds; temporal assertion corrected, failure retained.
green.*: exit0, 11 passed. final.*: exit0, 12 passed in1.67s.
Final tests cover those counterexamples plus active successor leases (late success/failure),
three expired deliveries then scoped archive, explicit stop and bounded backoff,
lost ACK response without redelivery, normal 44/74-second retries, all90 existing Go
delay fixtures, and two existing send-gate UNKNOWN/idempotency offline regressions.

Commands (working directory E:/WK/mq):
E:/RAG quention/.venv/Scripts/python.exe -B -m pytest tests/weknora_port/test_memory_queue_recovery.py -q --tb=short --junitxml=docs/weknora-port/evidence/mq-recovery-20261014/red.junit.xml
E:/RAG quention/.venv/Scripts/python.exe -B -m pytest tests/weknora_port/test_memory_queue_recovery.py tests/weknora_port/test_memory_queue.py tests/weknora_port/test_send_gate.py::test_call_id_reuse_never_reopens_ledger_and_upgrade_rechecks_permissions tests/weknora_port/test_send_gate.py::test_unknown_cannot_later_be_released_and_overrun_commits_unknown_then_blocks_month -q --tb=short --junitxml=docs/weknora-port/evidence/mq-recovery-20261014/final.junit.xml
git diff --check on tracked source changes: exit0. Full staged diff check reported12 trailing-whitespace lines in raw identity/red pytest XML/stdout; these failure receipts are preserved verbatim, not rewritten. Source/tests/report-only staged diff check: exit0.
Readonly final DB15 count: 0 keys.

## Safety and acceptance limits
ExtractionPayload is forwarded unchanged; no delivery token enters model operation identity.
extraction.py derives identity from owner/session/segment cursor, and model operation IDs
remain :extract:0 / :extract:1. memory_runtime.py uses that ID in memory/<identity>;
the same durable send gate still authorizes each transport send and rejects reused call IDs.
No changes to send_calls, reservation, UNKNOWN release, model configuration or runtime.
Checks of the gate use SQL stand-ins; no actual supplier request occurred.

SOURCE_READ / IMPLEMENTED: four bounded queue fixes.
OFFLINE_PASS: capped backoff,90 existing formula fixtures, existing UNKNOWN/idempotency checks.
ISOLATED_INTEGRATION_PASS: Redis delivery scenarios only.
REAL_PROVIDER_PASS / CUTOVER_PASS: NOTRUN. Normal HTTP/PG memory orchestration and full P4
equivalence are NOT established by these tests. Existing follow-up/cursor source untouched.
Serial integrator only needs memory_queue.py and dedicated tests; no shared runtime/config
or PG CAS changes. hashes.json binds final source/tests, red snapshot and raw receipts.

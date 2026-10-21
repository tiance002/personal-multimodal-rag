# P4 memory queue expired-lease reclaim grace — bounded implementation

Base commit: 143ddb74d43fa510d8d6a9cff335b88cd8f7e784.
Worktree: E:/WK/mq-grace; branch codex/weknora-mq-grace-20261014.
No merge, no push, no deploy; no runtime/retrieval/routing/frontend/business-DB edits; no real model calls.

## Upstream source position (asynq v0.26.0)

- `recoverer.go` `recoverLeaseExpiredTasks`:
  `cutoff := time.Now().Add(-30 * time.Second)`, then `r.broker.ListLeaseExpired(cutoff, ...)`;
  `if msg.Retried >= msg.Retry { archive } else { retry }`.
- `internal/rdb/rdb.go` `ListLeaseExpired` runs `listLeaseExpiredCmd` with `ARGV[1]=cutoff.Unix()`:
  `redis.call("ZRANGEBYSCORE", KEYS[1], "-inf", ARGV[1])` — upper bound is **inclusive `<=`**.
- Therefore a lease becomes reclaimable only when `lease_score <= now - 30s`. Go truncates the cutoff
  to whole seconds via `.Unix()`; the Python port keeps sub-second precision with the same operator.

Verified locally by shallow-cloning the tag: `git clone --depth 1 --branch v0.26.0 https://github.com/hibiken/asynq.git`.

## Behaviour before / after

Before: `CLAIM` reclaimed at `ZRANGEBYSCORE(KEYS[2]='-inf'..now)`, i.e. an expired delivery was
eligible for a new token the instant its lease passed. No grace.

After: the reclaim scan uses `-inf..now-RECOVERY_GRACE_SECONDS` (=now-30, inclusive). A lease keeps
its grace window; it is still *expired* for COMPLETE fencing (`until_time <= now` rejects late ACK),
but is only *reclaimable* at the upstream cutoff. A freshly expired lease is neither re-claimed nor
re-leased. The ready scan (`KEYS[1]`) still uses `now`, unchanged.

Not touched: ACK/retry/archive delivery-token checks, `source_retry_delay` (`n^4+15+jitter*(n+1)`,
PRE-increment), `max_retry`, retry consumption/archive rule (`retried >= max_retry`), PostgreSQL lease,
execution timeout, UNKNOWN and model-call rules.

## Files

- `backend/weknora_port/storage/memory_queue.py` — added `RECOVERY_GRACE_SECONDS=30`; `CLAIM` reads
  `ARGV[5]` as the reclaim cutoff; `run_due` passes `now - RECOVERY_GRACE_SECONDS`.
- `tests/weknora_port/test_memory_queue_recovery.py` — two new deterministic tests + step correction.

## Commands and exit codes

RED (before fix), isolated Redis DB15:
`E:/RAG quention/.venv/Scripts/python.exe -B -m pytest --noconftest tests/weknora_port/test_memory_queue_recovery.py -q -p no:cacheprovider -k grace --tb=short` → exit1.

GREEN (after fix), isolated Redis DB15, cwd E:/WK/mq-grace:
`E:/RAG quention/.venv/Scripts/python.exe -B -m pytest --noconftest tests/weknora_port/test_memory_queue_recovery.py tests/weknora_port/test_memory_queue.py -q -p no:cacheprovider --tb=short` → exit0, 17 passed in 1.40s.
`... -k` on the two new tests, `-v` → exit0, 6 passed: `[29.0-False] [0.001-False] [0.0-True] [-0.001-True] [-1.0-True]` + archive.
`git diff --check` → exit0. Read-only DB15 check after runs: `dbsize=0`, no `mq-grace:*` keys.

## Red / green record

RED (fix not applied): `2 failed, 4 passed, 9 deselected`, exit1.
Failed params `[29.0-False]` and `[0.001-False]` with
`AssertionError: assert ([] == ['...'])` on `assert leased==[job["id"]] and not ready and not dead`
— the un-graced scan reclaimed a lease that had expired only ~1s / 1ms past `now`.

GREEN (fix applied): all 6 new tests pass; the whole affected set is 17 passed.

Boundary semantics proven against real Redis (inclusive `<=` of `ZRANGEBYSCORE`):
just-expired inside grace → not reclaimed; 1ms above cutoff → not reclaimed; exactly `now-30` → reclaimed;
1ms and 1s past → reclaimed. After reclaim the job consumes retry with the existing delay formula
(`retry` 0→1, ready score = now+15) and archives at `retry==2` (`attempts=3`, `error_class=LeaseExpired`).

Existing recovery tests kept their intent: `Clock` step in the three lease-expiry tests changed from
`+601` (lease+1) to `+631` (lease 600 + grace 30 + 1), since reclaim now needs the extra grace.
The late-success/late-failure tests still hold `after == before`, proving a stale token cannot overwrite
or remove the successor lease; the explicit-stop test still passes (`q._stopped and not q.tasks`).
`tests/weknora_port/test_memory_queue.py` needed no change: its clock had already advanced ≥30s past the
seeded lease, so both the retry and recovery paths stayed valid.

## Safety and acceptance limits

Only exact unique-prefix keys (`weknora-python-port:20261013:mq-grace:*`) were written and deleted;
no flush, no config mutation, no legacy business instance touched.

VERIFIED: offline formula fixtures unaffected; isolated-Redis boundary/reclaim/archive/stop tests pass.
NOT RUN: `tests/weknora_port/test_memory_queue_native_pg.py` (needs real PG; does not exercise the reclaim
path; would overwrite the shared `P4_MQ_NATIVE_UNKNOWN_20261014.json` evidence artifact).
NOT RUN: full project regression, real provider, merge/push/deploy — out of scope.

Unverified: Go `.Unix()` whole-second truncation is not replicated (documented, deliberate — the port uses
float seconds with the same inclusive operator). No live clock-skew scenario beyond the deterministic
boundary injection was exercised.

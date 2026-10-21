# Task-2 correction — expired-lease reclaim in the integer-second domain

Base commit (kept): `e282b94572b45248a884c2b8182baa40a57acd7c`.
Worktree: `E:/WK/mq-grace`; branch `codex/weknora-mq-grace-20261014`.
Independent corrective commit on top of e282b94. Branch stopped afterwards; **not** merged into the main worktree.

## Why the previous patch was not equivalent

Upstream asynq v0.26.0:

- `recoverer.go` `recoverLeaseExpiredTasks`: `cutoff := time.Now().Add(-30 * time.Second)`, then `ListLeaseExpired(cutoff)`.
- `internal/rdb/rdb.go` `ListLeaseExpired`: `ZRANGEBYSCORE KEYS[1] "-inf" ARGV[1]` with `ARGV[1] = cutoff.Unix()`.
- `ExtendLease` writes the lease expiry as `expireAt.Unix()`.

Both the stored lease score and the cutoff are **Unix whole seconds**. e282b94 compared the float
deadline `E` with the float `now-30`: for `E=1700000600.75`, `N=1700000630.20` upstream reclaims
(`600 <= 600`) but e282b94 did not (`600.75 > 600.20`).

## Change (only the reclaim qualification)

- The lease zset score stays the original float deadline (`now+600`); historical leases are **not** rewritten.
- Reclaim eligibility is mapped to the integer domain: `floor(score) <= floor(now-30)`.
- Redis uses the **exclusive** upper bound `'(floor(now-30)+1)'`; an inclusive bound would over-reclaim one second.
- ACK/COMPLETE fence (`until_time <= now`, float) and claim token are unchanged.
- Untouched: retry formula, retry count, `max_retry`, PG 600s lease, 540s execution timeout, UNKNOWN handling.

## Redis parameter / Lua consumption evidence

`lua_bound.check.txt` (DB15, temp key deleted afterwards, no flush):

- `ZRANGEBYSCORE k -inf (1700000601` → `[below, exact, 0.75, 0.999]` → floor(score) <= K — **this is what CLAIM uses**.
- `ZRANGEBYSCORE k -inf 1700000601` → also returns `1700000601.0` → inclusive bound over-reclaims (must not use).
- `ZRANGEBYSCORE k -inf 1700000600` → misses `0.75`/`0.999` (the old float bug).

## Files

- `backend/weknora_port/storage/memory_queue.py` — `import math`; comment; CLAIM exclusive bound via `ARGV[5]`; `run_due` builds the bound.
- `tests/weknora_port/test_memory_queue_recovery.py` — new integer-domain and real-float-lease tests; corrected grace params.
- `docs/weknora-port/evidence/mq-grace-int-20261014/` — raw receipts.
- this report.

## Commands and exit codes (cwd `E:/WK/mq-grace`)

RED (before fix):
`E:/RAG quention/.venv/Scripts/python.exe -B -m pytest --noconftest tests/weknora_port/test_memory_queue_recovery.py tests/weknora_port/test_memory_queue.py -q -p no:cacheprovider --tb=short --junitxml=docs/weknora-port/evidence/mq-grace-int-20261014/red.junit.xml`
→ exit **1** (5 failed, 22 passed).

GREEN (after fix): same command with `green.junit.xml` → exit **0** (27 passed in 1.73s).

Bound check: `python -c "..."` → `lua_bound.check.txt`; DB15 `dbsize` returns to 0.

## RED / GREEN record

RED failures (all expose the float-vs-integer mismatch):

- `test_reclaim_uses_integer_second_domain[1700000600.75-1700000630.2-True]` — the upstream counterexample.
- `test_reclaim_uses_integer_second_domain[1700000600.999-1700000630.2-True]`.
- `test_reclaim_uses_integer_second_domain[1700000580.95-1700000610.9-True]` — other fractional phase.
- `test_reclaim_of_real_float_lease_matches_integer_cutoff` — a real delivered lease `1700000600.75` not reclaimed.
- `test_reclaim_cutoff_matches_upstream_inclusive_grace[0.001-True]` — old param encoded the float boundary.

GREEN: all pass. Late-ACK rejection, stale-token overwrite, and stop-does-not-resume tests still pass
(`after == before`; `q._stopped and not q.tasks`).

## Test key prefixes (this round)

- `weknora-python-port:20261013:mq:<uuid>:{ready,leased,jobs,dead}` — `fixture()`.
- `weknora-python-port:20261013:mq:<uuid>:foreign:…` — foreign-scope check.
- `weknora-python-port:20261013:mq-grace:<uuid>:…` — grace / integer-domain / real-float-lease.
- `weknora-python-port:20261013:mq-regression:<uuid>:…` — `test_memory_queue.py`.
- literal prefix `offline` — no Redis (Down() stub in the backoff test).

Cleanup: every test deletes exactly its own `q.keys` via `client.delete(*q.keys)`. No FLUSHDB/FLUSHALL;
DB15 ends at 0 keys.

## Source hashes (SHA-256)

- pre (e282b94 `memory_queue.py`): `D3A8546037EF90440B410BFBB5924A195A25694357B9F077CB5ED9302E2C8C04`
- post (this commit `memory_queue.py`): `F13E4A4B8A076A0A05719B1361802F6871A57B3FD62F03ABE1A561CEB8E70002`
- `test_memory_queue_recovery.py`: `AA0E0EE34F5D02F8B03AB64C0452728C1DA1477CE6DD765C83DC0FC015B6EECF`
- red exit code `1`, green exit code `0`.

## Unverified / not done

- `tests/weknora_port/test_memory_queue_native_pg.py` NOT RUN (needs real PG; does not exercise the reclaim
  path; would overwrite the shared `P4_MQ_NATIVE_UNKNOWN_20261014.json` evidence).
- No real clock-skew run beyond deterministic injection; no real provider call; no Ollama; no full regression;
  no merge/push/deploy.

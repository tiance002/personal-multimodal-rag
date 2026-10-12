# G4 task fence and read-only transport investigation

2026-10-12, continuation after commit84bee789. No model generation, service replacement, database mutation, quota reset, or release of UNKNOWN occurred in this continuation. Credential-free GETs to `https://api.deepseek.com/` are network diagnostics, not capacity/model/authentication proof.

## New files and isolated execution boundary

- `scripts/ordinary_verification_fence.py`: an opt-in G4 app factory for the three already-planned webpage questions. Normal API/Worker startup does not import or activate it. The owner must approve further calls after the latest UNKNOWN before its real grant/journal can be created or deployed.
- `backend/tests/test_ordinary_verification_fence.py`: simulated grants/calls, SQLite token-state fixtures and mocked authoritative guard delegates; actual native Linux file locking, atomic write/fsync, restart and two-process contention. No provider calls or production DB writes.
- `scripts/probe_ordinary_output.py`: separate explicit acknowledgment of the exact latest UNKNOWN. A single fresh DeepSeek4096 follow-up may use `--new-probe-label owner-followup-1`; it gets a new Run/Attempt and never overwrites/resends the existing receipt. Seven-call/75,000-token caps remain unchanged; any other new UNKNOWN still stops.
- `backend/tests/test_output_probe_failure.py`: separately tests that the prior acknowledgment does not authorize the latest or a future UNKNOWN.
- These notes and `progress.md`: actual results, proposed next actions and permission boundary.

The fence pins a local owner-grant SHA, exactly three fresh Run UUIDs and the hashes of every existing probe receipt. Its fixed deployed root is `/data/storage/weknora-probes-20261012`. It counts **full** prior probe bounds (currently10030Token) plus every newly admitted embedding/generation request bound. Unsent failures remain conservatively counted. A missing/corrupt journal, changed probe snapshot, reused request identity, changed policy, wrong Run/Scope or aggregate over75000 is rejected. It never reinitializes an absent journal.

Only the authorized original session `313909f3-34d9-4f26-8778-3d5bd27f2c60`, KB `d78f3434-0e8f-40bf-afcb-f1c2c311ff51`, document `1fd5a928-d3d7-4bae-aa00-fc74701f61e5` and original embedding/cheap/expensive policy IDs may dispatch. The existing guard instances, Token/cost ledger, policy fingerprints, egress checks, lifecycle and exact wire measurement remain authoritative and are delegated without replacement. Vision/rerank are denied for this task. A read-only lookup of this task's token identities stops on new UNKNOWN both before reservation and immediately before send; this also prevents an automatic expensive fallback after UNKNOWN. Known truncation/settled usage may still follow the original routing policy.

Task-only middleware permits mutation solely through the original session's message endpoint and cancellation of the three fresh task Runs. It blocks model-configuration changes that could rebuild an unfenced container. This temporary test entrypoint is not a new product feature, policy or database table.

No real owner grant or admission journal has been created. A local ignored draft `var/reports/weknora-ordinary-20261012/web-owner-grant.pending.json` is expressly PENDING_NOT_AUTHORIZED and rejected by the loader; it is outside the fixed deployed root and acknowledges no UNKNOWN. It must be rebuilt from the final probe snapshot only after explicit new approval. Initialization must use exclusive creation; never overwrite an existing journal. Real installation/activation and provider-backed operation: **NOT RUN**.

## Concrete proposed replay

Local ignored `web-replay.pending.json` reserves UUIDs only; no Run was inserted and no POST was sent:

| Question | Existing policy result | Fresh Run |
|---|---|---|
| 我应该怎么学习 | STANDARD2048 | e7f82931-0b2f-4d1d-a12f-263633db4741 |
| 请简短概括文档推荐的学习方法，并引用原文。 | SHORT1024 | 7b90d6f9-0a15-4380-a119-e248c59b0d52 |
| 请根据文档详细解释其中推荐的学习方法，并对每种方法分别说明原理、实施步骤和注意事项；最后比较不同方法的适用场景，引用对应原文。 | LONG4096 | 31c5266c-55a7-4377-9338-01da73bec415 |

The tiers were computed with the existing actual `detect_intents/ordinary_generation_budget`; routing score/model choice was not forced. Proposed preceding probes are at most one fresh Xing4096 and one fresh DeepSeek4096. Total would be6of7probes, not a quota expansion. All calls remain stopped pending explicit owner authorization after Run72917fff UNKNOWN.

## Commands/results

```powershell
docker exec -e PYTHONPATH=/tmp/verification-fence-test:/app raglocalfirst0930-api-1 python /tmp/verification-fence-test/test_ordinary_verification_fence.py
```

Source/test files were copied into the API container's `/tmp/verification-fence-test/` only. Final command **exit0,10tests OK**. This validates the task fence's native filesystem/process behavior and SQLite/mock integration, not real PG/provider-backed deployment. Windows skips this Linux-specific class explicitly; no substitute lock is used in execution.

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend/tests/test_output_probe_failure.py -p no:cacheprovider
```

Final **exit0,6passed**. Existing unaffected capacity/UI/SSE tests are reused, not rerun.

```powershell
docker exec -e PYTHONPATH=/app raglocalfirst0930-api-1 python /tmp/deepseek-network-readonly.py
```

**exit0**, credential-free root GETs only, generation_calls0. First observation: default direct HTTP401, ordinary observed `_ConnectionNotSent` (its specific internal code was not captured in that first diagnostic). Second observation: default direct `URLError/SSLEOFError`, ordinary observed HTTP401. The changed outcomes support an inference of intermittent connection behavior; they do not isolate the cause of the original UNKNOWN or justify changing transport/ALPN/certificate checks. Current safe local evidence: `var/reports/weknora-ordinary-20261012/deepseek-network-readonly.json`; first observation also remains in tool output. Neither UNKNOWN was reclassified or released.

```powershell
.\.venv\Scripts\python.exe var/reports/weknora-ordinary-20261012/prepare-replay-draft.py
```

**exit0**, three question tiers and fresh UUIDs prepared, provider_calls0, statePENDING_NOT_AUTHORIZED. No active grant, service switch or database Run creation.

Source search attempted a nonexistent `generation_budget.py` and `budget_gate.py`; both reads failed and were not verification. Actual generation policy is `answer_hardening.py`; ledger lookup uses `service_token_reservations` directly. No command failure is relabelled PASS.

## Current acceptance

Task fence **PASS offline/native Linux**, real activation **NOT RUN**. Real G4 question/answer/citation/reload **NOT RUN**. Existing actual Xing2048 capacity PASS remains valid; Xing4096 and ordinary DeepSeek still unverified. Overall `/readyz` remains503, Goal not accepted. No G5 cleanup, Push, Tag or release.

Fresh owner decision is required by the previously approved “another UNKNOWN means immediate stop” condition. It must authorize the two fresh probes, temporary task-only API entrypoint for the three original-scope webpage replays, and continued inclusion of both UNKNOWN reservations in the unchanged75000Token aggregate. Any new UNKNOWN must halt again. **未实现的命令不得报告通过。**

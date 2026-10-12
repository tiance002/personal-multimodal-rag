# G1 ordinary output budget evidence

Baseline: `b4aa3ec5ff82497fdf9f9f00cdacf75e3128edd3`. Scope: ordinary Quick generation budget, provider local protocol counters, response truncation, and actual Quick progress hooks. No deployed service, provider request, secret read, production database operation, grant/manifest rewrite, push or tag was performed by this worker.

## Implemented source

- `answer_hardening.py`: immutable ordinary SHORT 1024 / STANDARD 2048 / LONG 4096 policy based only on explicit requested answer length and existing detected intent count. Legacy generation policy retained for frozen/other execution.
- `quick_chain.py`: explicit ordinary policy opt-in; one immutable budget supplies prompt, context output reservation and generation envelope. Removes ordinary 180-character/100-word/three-suggestion instructions. Each ordinary attempt copies the base envelope with its request identity; cheap and expensive attempts have distinct immutable envelope identities and the same chosen output budget. Retrieval started/completed/failed, prepared evidence count, generation and candidate validation progress are emitted from actual code boundaries; failed callback delivery cannot change answer behavior.
- `routes.py` `_answer_service`: ordinary service opts into ordinary budgets rather than setting the frozen 512 cap.
- `siliconflow_xing_capacity.py`, `v6_text_capacity.py`: separate ordinary counter classes permit reviewed local 1024 framing (and existing 512 protocols), explicitly reject 2048/4096 as `OUTPUT_CAPACITY_UNVERIFIED`, and retain the frozen counter contracts unchanged.
- `service_capacity.py`, `service_configuration.py`: load ordinary counter paths, remeasure exact wire bytes and expose local reviewed caps, endpoint evidence UNKNOWN and hosted recipe equivalence UNKNOWN. Real ordinary requests above 512 fail before provider send because endpoint output capability was not proven; simulated protocol runs can verify local 1024.
- `cloud.py`: `finish_reason=length` becomes specific truncation even when content is empty; preserves usage settlement before rejection and blocks unverified real output capacity.
- `test_ordinary_output_budget.py`, `test_service_usage.py`: cover tiers, immutable budget, complete prompt, envelope/wire output reservation, frozen contract rejection, higher tier rejection, unknown readiness, cheap truncation then expensive attempt, progress fields, empty/nonempty length, received-usage settlement at 1024 and actual local pinned Xing framing.

Root owns `GenerationEnvelope.attempt_id` and readiness integration. Legacy envelope identity includes no new field when attempt_id is absent, preserving historical hashes. No routing score/threshold, retrieval weights, ServiceUsageGuard, final validator, citation atomicity, caption/vision 512 or historical ledger IDs were modified by this worker.

## Executed commands and results

Initial command (exit 1):

```powershell
./.venv/Scripts/python.exe -m pytest -q backend/tests/test_v6_text_capacity.py backend/tests/test_siliconflow_xing_capacity.py backend/tests/test_service_configuration.py backend/tests/test_v6_output_cap.py --tb=short -p no:cacheprovider
```

Result: 49 passed, 70 setup errors because `XING_TEST_ASSETS` was unset. Existing verified assets were then located from the repository audit evidence. No download or guard relaxation was used.

Intermediate focused command below (exit 1): 7 failed, 206 passed. New Chinese regex/test literals were damaged by Windows PowerShell pipeline encoding. Corrected the new literals using apply_patch; existing source literals were preserved. Repeating it before the last four additional checks returned exit 0, 213 passed.

Final focused command (exit 0):

```powershell
$env:XING_TEST_ASSETS = 'E:\RAG quention\var\model-assets\xing4.0\baae3c3e813cad5f888f1f485cfff659c89076c5'
./.venv/Scripts/python.exe -m pytest -q backend/tests/test_ordinary_output_budget.py backend/tests/test_answer_hardening.py backend/tests/test_v6_text_capacity.py backend/tests/test_siliconflow_xing_capacity.py backend/tests/test_v6_output_cap.py backend/tests/test_service_usage.py backend/tests/test_service_configuration.py backend/tests/test_service_caption.py backend/tests/test_p5_router_generation.py backend/tests/test_quick_chain_budget.py --tb=short -p no:cacheprovider
```

Result: **217 passed, 7 warnings in 14.89s**. Warnings are existing SWIG deprecation warnings. Execution returned actual pytest exit 0. This includes SIMULATED provider/SQLite quota tests and REAL OFFLINE pinned local Xing resource tests; none call hosted models.

After the final progress-only correction (candidate validation exceptions leave the already completed generation stage intact), reran the affected tests:

```powershell
./.venv/Scripts/python.exe -m pytest -q backend/tests/test_ordinary_output_budget.py backend/tests/test_p5_router_generation.py --tb=short -p no:cacheprovider
```

Result: exit 0, **58 passed, 7 warnings in 0.59s**.

Additional command (exit 0):

```powershell
git diff --check -- backend/app/application/quick_chain.py backend/app/application/answer_hardening.py backend/app/adapters/models/cloud.py backend/app/adapters/models/service_capacity.py backend/app/adapters/models/service_configuration.py backend/app/adapters/models/siliconflow_xing_capacity.py backend/app/adapters/models/v6_text_capacity.py backend/tests/test_service_usage.py
```

Result: no whitespace errors (Git reports its existing LF/CRLF conversion warning).

## Readiness and limits

- Status: **SOURCE_IMPLEMENTED / OFFLINE_PASS** for G1 local contracts. Real provider output evidence and hosted renderer equivalence: **UNKNOWN**.
- Real ordinary endpoint capacity probe, real original-question run, deployed API/Worker/UI verification and real citation HTTP replay: **NOT RUN**. G1 offline consistency does not establish hosted endpoint support or E2E_PASS.
- Real 1024 ordinary generation: **BLOCKED — OUTPUT_CAPACITY_UNVERIFIED** pending authorized endpoint proof. 2048/4096 ordinary tiers: **UNVERIFIED**, explicitly unavailable; never silently reduced to 512 or 1024.
- No new endpoint-proof bypass/config toggle was introduced. Existing owner policy IDs, monthly quota, UNKNOWN occupancy and frozen grant authorization remain authoritative.
- No commit made by this worker. Root coordinates final inspection, staging and any local commit. No milestone acceptance or tag is claimed.

未实现的命令不得报告通过。

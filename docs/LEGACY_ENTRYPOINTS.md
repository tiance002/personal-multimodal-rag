# Explicit legacy tools

The default wheel excludes backend.app and backend.tests. No legacy service, script or experiment is a new-chain prerequisite. Retained entries remain in the source checkout; specifically retired scripts are recoverable from Git as recorded below; their old grants do not authorize new sends.

- Previous API/ingestion source configuration: deploy/legacy/compose.legacy.yml and Dockerfile.*.legacy. Running it is an explicit, separately approved service action; no command is executed here.
- backend.app.main:app, backend.app.workers.ingestion: previous API and old text-job polling worker.
- scripts/ordinary_verification_fence.py: previous grant-specific TaskFence startup, never imported by the new API.
- scripts/model_probe.py: retained historical provider probe, called only by the explicit legacy verification wrapper; not a native startup check and not authorized to run by this migration.
- scripts/evaluate_rag_quality.py: old QuickChain evaluation. Six-package new-chain evaluation remains under eval_center/weknora*.
- P8.1 (2026-10-18): legacy React selection has been removed. `VITE_RAG_IMPLEMENTATION` no longer selects any UI; the sole entry is WeKnoraPythonApp. Old App source and historical tests remain reference material.
- `scripts/start_backend.py` now exits nonzero with a retirement notice. Use `wk.ps1 start|stop|status|check -Config <explicit local JSON>`; it never imports the old API or dotenv.

Ledger, UNKNOWN, Run/Attempt, Gold, original source versions, references, reports and backups are retained. This batch deletes no old tracked source. After independent functional/cutover acceptance, any retirement must name exact files and their remaining callers in a separate reviewed commit.

## Explicit retained legacy checks (2026-10-15)

- Historical Python tests: `python -m pytest -c pytest.legacy.ini`. This preserves backend/tests, eval_center/tests and their original conftest/assertions, including known failures/dependency limitations; it is not part of a claimed whole-repository GREEN.
- Historical frontend test configuration remains in source for evidence/recovery; it no longer has a selectable legacy UI and is not a supported startup/acceptance command. Current adapter tests target the sole new UI with explicit API configuration. Historical browser status in older reports is unchanged.
- `scripts/legacy/eval.ps1` preserves the old Schema/Retrieval/Quality dispatch. `scripts/legacy/verify-m0.ps1` preserves the old test/probe/schema workflow with the explicit legacy pytest config. These are historical operational commands, not authorized to run here: the latter may start Compose, migrate the old DB and send model probes. Their existence does not grant permission.
- Native eval explicitly distinguishes verify-existing (read existing results plus isolated DB) from execute-native (fresh original ingestion/four-mode execution using closed model fixtures). No old default QuickChain evaluator is dispatched. Missing inputs/output/isolation permission fail closed; simulated metrics never become real quality claims.
- This entry migration deletes no files. Legacy App is no longer reachable from the normal entry; its source remains because historical callers/tests are retained. Old grants/journals/probe evidence remain unchanged. See the latest P8.1 report for exact container candidates awaiting separate deletion approval.

## Specifically authorized source-only retirement

On2026-10-15 only scripts/smoke_ordinary_deepseek_tls.py, scripts/smoke_ordinary_output_capacity.py and scripts/smoke_service_capacity_linux.py were removed after the new exact-file Owner approval and zero-active-caller/hash checks. Source recovery point is e20b5d9. Existing reports/tokenizer/recipe assets and all other candidates/support code are retained. See THREE_SMOKE_RETIREMENT_REPORT_20261015.md; this grants no browser privilege, service replacement or broader deletion.

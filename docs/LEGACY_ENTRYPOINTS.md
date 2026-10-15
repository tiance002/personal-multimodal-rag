# Explicit legacy tools

The default wheel excludes backend.app and backend.tests. No legacy service, script or experiment is a new-chain prerequisite. These remain in the source checkout for history/recovery; their old grants do not authorize new sends.

- Previous API/ingestion source configuration: deploy/legacy/compose.legacy.yml and Dockerfile.*.legacy. Running it is an explicit, separately approved service action; no command is executed here.
- backend.app.main:app, backend.app.workers.ingestion: previous API and old text-job polling worker.
- scripts/ordinary_verification_fence.py: previous grant-specific TaskFence startup, never imported by the new API.
- scripts/smoke_ordinary_output_capacity.py and scripts/model_probe.py: historical provider probes, not startup checks and not authorized to run by this migration.
- scripts/evaluate_rag_quality.py: old QuickChain evaluation. Six-package new-chain evaluation remains under eval_center/weknora*.
- Optional old React build: explicitly set VITE_RAG_IMPLEMENTATION=legacy. The unset default is weknora_python; unknown values fail rather than silently choosing legacy.

Ledger, UNKNOWN, Run/Attempt, Gold, original source versions, references, reports and backups are retained. This batch deletes no old tracked source. After independent functional/cutover acceptance, any retirement must name exact files and their remaining callers in a separate reviewed commit.

## Explicit retained legacy checks (2026-10-15)

- Historical Python tests: `python -m pytest -c pytest.legacy.ini`. This preserves backend/tests, eval_center/tests and their original conftest/assertions, including known failures/dependency limitations; it is not part of a claimed whole-repository GREEN.
- Historical frontend tests: `npm test -- --config=playwright.legacy.config.ts` in frontend; explicitly selects legacy UI. Default Playwright selects only tests/weknora-port, leaves VITE_RAG_IMPLEMENTATION unset, uses strict port ownership and never reuses another server. Actual browser acceptance remains BLOCKED.
- `scripts/legacy/eval.ps1` preserves the old Schema/Retrieval/Quality dispatch. `scripts/legacy/verify-m0.ps1` preserves the old test/probe/schema workflow with the explicit legacy pytest config. These are historical operational commands, not authorized to run here: the latter may start Compose, migrate the old DB and send model probes. Their existence does not grant permission.
- Native eval explicitly distinguishes verify-existing (read existing results plus isolated DB) from execute-native (fresh original ingestion/four-mode execution using closed model fixtures). No old default QuickChain evaluator is dispatched. Missing inputs/output/isolation permission fail closed; simulated metrics never become real quality claims.
- This entry migration deletes no files. A separately authorized three-script follow-up must be a different commit; all other retirement remains prohibited. New caller migration is not deletion authorization. Legacy App remains explicitly selectable and cannot yet be deleted; old grants/journals/probe evidence remain unchanged.

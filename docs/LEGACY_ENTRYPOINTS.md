# Explicit legacy tools

The default wheel excludes backend.app and backend.tests. No legacy service, script or experiment is a new-chain prerequisite. These remain in the source checkout for history/recovery; their old grants do not authorize new sends.

- Previous API/ingestion source configuration: deploy/legacy/compose.legacy.yml and Dockerfile.*.legacy. Running it is an explicit, separately approved service action; no command is executed here.
- backend.app.main:app, backend.app.workers.ingestion: previous API and old text-job polling worker.
- scripts/ordinary_verification_fence.py: previous grant-specific TaskFence startup, never imported by the new API.
- scripts/smoke_ordinary_output_capacity.py and scripts/model_probe.py: historical provider probes, not startup checks and not authorized to run by this migration.
- scripts/evaluate_rag_quality.py: old QuickChain evaluation. Six-package new-chain evaluation remains under eval_center/weknora*.
- Optional old React build: explicitly set VITE_RAG_IMPLEMENTATION=legacy. The unset default is weknora_python; unknown values fail rather than silently choosing legacy.

Ledger, UNKNOWN, Run/Attempt, Gold, original source versions, references, reports and backups are retained. This batch deletes no old tracked source. After independent functional/cutover acceptance, any retirement must name exact files and their remaining callers in a separate reviewed commit.

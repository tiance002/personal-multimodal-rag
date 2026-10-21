# P7 capture batch — paused by Owner

Owner requested closing the current work and waiting. This is a partial implementation checkpoint, not P7 acceptance.

## Changes

- `eval_center/weknora_frozen_gold.py`: strict read-only frozen-manifest/file verification; five datasets × eight QA cases and 24 Omni parsing pages. Gold stays evaluation-only. MML page base remains UNKNOWN; Omni independent premodel QA overlay remains absent.
- `backend/weknora_port/service.py`: ordinary entry uses one reusable `prepare_turn` preparation and retains search/rerank/context rankings.
- `generation/types.py` and `generation/context.py`: freeze prompt rendering time for four variants of the same captured input.
- `runtime.py` and `eval_center/weknora_paired.py`: private evaluation binding for original OFF baseline, Cheap-only, Expensive-only and Dynamic; one prepared context. Existing model/send authorities remain required; no public mode override or new real-call authorization.
- `eval_center/weknora_scoring.py`: native document projection, source-backed project diagnostics and explicit simulated/unknown metric status. Official model quality acceptance remains NOT RUN.
- Tests cover these boundaries; PG test adds native four-mode integration but has not completed in the current environment.

## Evidence

- Frozen-source verification: `evidence/RESUME_P7_FROZEN_GOLD_20261014.json`, stdout and exitcode (0).
- Affected service/generation/scoring tests: `evidence/P7_CAPTURE_MAIN.{stdout.txt,junit.xml,exitcode.txt}`: 112 passed, exit 0. Initial missing render-field failure is preserved under `P7_CAPTURE_MAIN_RENDER_FIELD_RED`.
- Command executed at closing: `E:/RAG quention/.venv/Scripts/python.exe -B -m pytest tests/weknora_port/test_native_paired.py -q --junitxml=docs/weknora-port/evidence/P7_NATIVE_PAIR_OFFLINE.junit.xml` with the dedicated runtime-deps PYTHONPATH. 2 passed, exit 0. This uses explicit doubles and is an OFFLINE mechanism test, not provider/PG acceptance. Earlier frozen-config assignment failure is retained as `P7_NATIVE_PAIR_OFFLINE_FROZEN_FIXTURE_RED`.
- `P7_NATIVE_PAIRED_HTTP` and `P7_NATIVE_PAIRED_HTTP_ENGINE_UNAVAILABLE`: integration attempt exited 1 before PG connection, ConnectionTimeout; Docker API pipe `dockerDesktopLinuxEngine` absent. No service restart or replacement was attempted.

## Status and limitations

SOURCE_READ / IMPLEMENTED for the bounded capture/scoring paths; bounded OFFLINE_PASS as above. Current native paired PG/HTTP integration BLOCKED, REAL_PROVIDER_PASS and CUTOVER_PASS NOT RUN. No whole-stage PASS.

Ledger records are owner-window differences, not independently proven causal attribution under concurrent background calls. Real-provider receipts still require verification. Full frozen-dataset native ingestion, ranking and legitimate scoring remain pending. Omni geometry output, exact Asynq behavior and broader source parity remain open. P8 wheel build still lacks available setuptools>=75; no dependency download/install attempted. Existing historical failures, data, UNKNOWN and old workspaces retained.

## Resume

Read this note, PAUSE_RESUME_20261013.md and actual HEAD/status first. Wait for explicit Owner continuation. Then resolve dedicated integration-runtime availability without modifying old services and run the pending native pair integration. Keep real-provider, old service/config/key/database, UNKNOWN-release and push/tag/release prohibitions. Do not claim completion from the offline tests.

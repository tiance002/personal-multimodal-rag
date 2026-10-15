# Final production-source wheel and retirement review, 2026-10-15

Final production source b5f396dc5d48d64dd6f4825524e872f0967d3402 includes P8 entry/worker fix9567c88, source reference DTO correction4488ac1 and missing-summary-model correctionb5f396d. This is a new immutable artifact, not reuse of the old9567c88 wheel. Documentation/inventory receipts follow separately. No deployment or deletion.

## Actual commands and results

All evidence below: docs/weknora-port/evidence/P8_FINAL_PACKAGE_20261015/. Existing installed Python dependencies are reused as explicit read-only paths; full fresh dependency installation/resolution is NOT CLAIMED.

1. `E:/RAG quention/.venv/Scripts/python.exe -B scripts/weknora_port_package.py --ref b5f396dc5d48d64dd6f4825524e872f0967d3402 --output E:/WK/pkg/p8-final-b5f396d-20261015`: exit0;161 native wheel members match raw Git bytes. build.stdout/exitcode.txt and wheel.manifest.json.
2. `E:/RAG quention/.venv/Scripts/python.exe -B docs/weknora-port/evidence/P8_FINAL_PACKAGE_20261015/install_verify.py`: exit0. Creates a new E-drive venv and runs pip --python <new venv Python> install --no-index --no-deps <new wheel>. Confirms wheel excludes backend.app/backend.tests, contains reference coordinate and summary terminal branches, and runs weknora-python-api/worker/init --help (all0). install.stdout/exitcode.txt, installed-entry.receipt.json, wheel-install.stdout.txt and *.help.txt.
3. `E:/WK/pkg/p8-final-b5f396d-20261015/installed/Scripts/python.exe -I -B docs/weknora-port/evidence/P8_FINAL_PACKAGE_20261015/installed_verify.py`: exit0;4 passed21.20s,3 retained pytest/deprecation warnings. Actual loopback HTTP new entry/independent worker, reference two-turn JSONB/history readback, absent and configured summary ASGI upload/index/QA. All actual dedicated PG/Redis, model transport synthetic. 97 backend modules from installed site-packages and97 byte hashes match package manifest;0 source backend imports. installed.stdout/exitcode.txt and installed.receipt.json.

Wheel: E:/WK/pkg/p8-final-b5f396d-20261015/dist/personal_rag-0.1.0-py3-none-any.whl. SHA256 aebf8eb475dad38f45c8267071771f97b10adeed007e9acc80dad17498b053e8;2833545bytes. Existing older wheel and all earlier successful/failed receipts retained. Each test writes a new UUID-named synthetic result, not overwriting prior evidence. Actual supplier requests0. React build and compose config proofs from unchanged entry/config inputs are reused, not rerun; no253/27/8 historical whole-suite repetition.

## Summary-model classification

This was a Python production difference, now minimally corrected and separately committed. No default Chat selection was added. Fixed upstream missing-summary-model task is terminal with summary_status=failed; parent parsing/indexing may complete. Both actual branch tests assert persisted summary state, generated chunk/call counts, actual enabled embeddings and completed QA/source references. See SUMMARY_CONFIGURATION_REPORT_20261015.md for RED/GREEN7checks and exact source branches. No new full Go upload run is claimed; source read and Python isolated execution remain separately labelled.

## Exact retirement review deliverable

P8_RETIREMENT_REVIEW_20261015.md and evidence/P8_RETIREMENT_REVIEW_20261015/inventory.json list45 exact existing tracked candidate files, per-file SHA, AST import and explicit-text caller locations, outside-candidate dependencies, plus21 protected support-code hold paths. `python -B scripts/weknora_port_retirement_inventory.py`: exit0; no absent candidate paths or Python parse errors;0 AST imports from new backend/weknora_port into those candidates. Frozen candidate/source scan b5f396d. Script is retained for reproducibility and performs no deletion/runtime imports.

Concrete remaining caller blockers: old QuickChain is still imported by backend/app/api/routes.py and backend/app/bootstrap.py; CloudAdapter retains old provider/budget/test callers; old Redis/SSE adapter is referenced by run construction/provider-usage support; frontend/src/main.tsx still has explicit legacy App lazy import; deploy/legacy intentionally names old services. Exact line locations and affected tests are in JSON. None is automatically approved for deletion. Candidate list is a bounded first retirement review, not a proposal to remove whole directories or all old modules. Any outside-candidate caller must be ported, explicitly retired or retained with its dependency. Ledger/UNKNOWN/Run/Attempt/Gold/source/version/history/migration/backups are held. Report details affected contract/import, installed-wheel, React, real isolated ingest/QA/reference/history/memory/image/replay checks required after a separately approved later deletion.

## Status, protection and remaining boundaries

Scoped final installed-code ISOLATED_INTEGRATION_PASS. Not REAL_PROVIDER_PASS/CUTOVER_PASS or complete Goal acceptance. Production send authority remains absent by default, with ready503/DENY_UNCONFIGURED until a separately approved real server-side permission/cost binding. No model downloads/pulls, real calls, image build, schema initializer, compose up, production configuration/key/service modifications or push/tag/release. No code/data deletion or UNKNOWN release.

Four existing untracked protected files retain exact hashes and remain untracked. Original SERIAL_INTEGRATION receipt hash a9796d6056f33c38d177387d7679cac6b170a75346d46f1837af284a8dff4f33 unchanged; tracked historical evidence modified0. protection.json and verification-sources.json record final boundaries. Independent reviewer owns full paired Go/Python assessment and deletion approval; this batch changes neither upstream behavior nor routing threshold/switch policy.

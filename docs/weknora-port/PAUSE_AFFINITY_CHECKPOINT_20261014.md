# Owner pause checkpoint — affinity / KB selection, 2026-10-14

Status: PAUSED_BY_OWNER, WIP NOT ACCEPTED. Latest Owner instruction: 收尾目前工作，等待之后继续. No further fixes, builds, browser acceptance or next batch started after that instruction.

Workspace: E:/RAG-Worktrees/weknora-python-port-20261013
Branch: codex/weknora-python-port-integrate-image-20261014
Last reviewed stable baseline: 143ddb74d43fa510d8d6a9cff335b88cd8f7e784.
This checkpoint contains incomplete affinity/KB-selection implementation and retained failure evidence. It is a local rollback/recovery commit, not stage PASS, release or cutover approval.

## Current changes

- retrieval/memory_affinity.py and retrieval/pipeline.py: fixed-source bounded familiar-document score conditioning after rerank; runtime binds trusted memory context.
- model_selection.py and runtime.py: normal KB/model selection branches, literal remote source and requested/profile validation.
- storage/turns.py: request KB ordering snapshot and identity, preserving legacy completed replay intent.
- frontend MemoryPanel.tsx, WeKnoraPythonApp.tsx and api.ts: familiar-document tab, open original document, confirm stop tracking. Unbuilt and browser-unverified.
- Exact extracted upstream affinity/model-selection oracle script, fixtures, offline and isolated HTTP tests. The oracle is not a full Go ordinary entry comparison.

## Actual verification

Command: E:/RAG quention/.venv/Scripts/python.exe -B -m pytest --noconftest tests/weknora_port/test_affinity_pipeline.py tests/weknora_port/test_kb_model_selection.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_turn_storage.py -q -p no:cacheprovider --tb=short
Result: OFFLINE_PASS, 80 passed in 1.45s, exit 0. Evidence: evidence/P4_AFFINITY_KB_GREEN_20261014.stdout.txt and .exitcode.txt.

Command: E:/RAG quention/.venv/Scripts/python.exe -B -m pytest --noconftest tests/weknora_port/test_affinity_kb_http_pg.py -q -p no:cacheprovider --tb=short --junitxml=docs/weknora-port/evidence/P4_AFFINITY_KB_ENTRY_20261014.junit.xml
Result: FAIL, 1 failed in 10.25s, exit 1. At test_affinity_kb_http_pg.py:55 reversed KB-order replay returned HTTP 200, expected 409. Test stopped there; later affinity/delete HTTP assertions were NOT RUN. Keep original stdout/exit/JUnit unchanged. Determine admission/replay behavior and fixture expectation from source before fixing; do not weaken assertions merely to pass.

React build: NOT RUN. Actual browser document controls: NOT RUN. Full Go ordinary engine comparison: NOT RUN. Real providers / cutover / push / tag / release: NOT RUN.

## Resume points and open source boundary

1. Resolve the isolated HTTP replay-order failure with a minimal reproduction, preserving old hashes/rows and UNKNOWN. Confirm whether current test demonstrates a production issue or test setup mismatch.
2. Run bounded frontend build and actual owned browser acceptance for the new document controls; no existing user services/profile mutation.
3. Finish the bounded stage report and ask parent independent review after evidence exists.
4. Model representation remains a structural SOURCE_PARITY gap: fixed Go uses separate Source(local/remote) and Parameters.Provider; existing Python model rows store vendor identity in source and depend on immutable configuration hashes. Do not silently migrate existing rows, infer source from URLs, or claim the extracted selector oracle proves complete model factory parity. Parent needs the smallest explicit mapping plan before a full ordinary-entry comparison.

No MQ grace changes or repeated delivery tests in this batch. No real provider requests, existing service/config/key changes, old DB/volume/source modifications, UNKNOWN release or deletion. Isolated test fixture data is retained. Test process completed; no browser fixture was started. The original workspace and learning snapshot remain outside this checkpoint.

未实现的命令不得报告通过。
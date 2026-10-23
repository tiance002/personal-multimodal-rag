# S8.8 workflow B — reliability implementation and offline evidence

Status: IMPLEMENTED / OFFLINE_PASS; real Provider and real selected-asset PostgreSQL switch NOT_RUN.

## Implemented

- Caption completion consumes normalized provider finish semantics (`stop`, `end_turn`, `stop_sequence`, `completed`), distinguishes length, empty body, missing completion and other incomplete reasons. Partial text and nullable actual usage remain visible; failed Caption never creates `image_caption` evidence. OCR is durably checkpointed before Caption dispatch. Summary enrichment excludes explicitly incomplete derivatives.
- Image settings independently freeze `reasoning_mode`, finite `max_output_tokens` (0 -> 5000) and generation config version. Calls supply explicit `ChatOptions`; shared adapter capability validation belongs to workflow A.
- Selected image retry creates an immutable new task keyed by idempotency hash and exact source version / asset path / SHA / config snapshot. UNKNOWN or active prior work blocks another attempt. Worker reads only that immutable asset, skips the unselected sibling and document parser, prepares one result and its embedding, then inserts and switches indexes in one transaction. Existing evidence is retained and never deleted; failed preparation/switch leaves it active. Uncertain embedding dispatch is checkpointed and is not automatically replayed.
- Migration `017_image_selected_retry.sql` adds only `retry_key` and extends the original task uniqueness constraint. Apply via `apply_image_selected_retry_schema` after migration010 and before updated API/Worker. No historical task/ledger is rewritten.
- Fixed document clarification renders at most five 120-character titles, retaining all documents in the existing selection UI. Collector permits only explicit server-fixed lifecycle completion with no provider attempts; provider finish/truncation checks stay strict.
- Cancellation records observed user/deadline origin, latest observed stage, deadline kind, and failing-call sent flag. Missing generation-send and settled-call telemetry stays null/UNKNOWN. Cancelled History stores the same diagnostics as SSE. Historical HTTP112's precise origin remains unproven; no fabricated reconstruction.

## Evidence basis

Read existing private `ECS118_IMAGE_LENGTH_DIAGNOSIS.md` and HTTP099/112 portions of `ECS118_FAILURE_QA_REVIEW.md` under the controlled local report directory. Historical HTTP099 fixed answer had complete SSE but no provider finish; historical HTTP112 had three settled preprocessing calls and no generation, but origin/final persistence remain missing. No private source text was copied into public fixtures.

## Executed commands

All commands ran in the S8.8 managed worktree, using `E:\RAG quention\.venv\Scripts\python.exe`.

1. `-m pytest tests/weknora_port/test_image_processing.py -q` — RED exit1, 2 failed / 7 passed: length and missing finish were incorrectly indexed.
2. `-m pytest tests/weknora_port/test_image_processing.py tests/weknora_port/test_image_configuration.py -q` — GREEN exit0, 22 passed.
3. Initial new reliability test import mistakenly named RuntimeService; collection exit1. Corrected fixture import, no production workaround.
4. `-m pytest tests/weknora_port/test_s88_reliability.py tests/weknora_port/test_service.py tests/weknora_port/test_service_task_cleanup.py -q` — exit0, 48 passed.
5. `-m pytest tests/weknora_port/test_s88_image_retry_pg.py -q` — exit0, 1 skipped because explicit disposable DSN/system ID absent; real PostgreSQL NOT_RUN in B's local environment.
6. `-m pytest tests/weknora_port/test_s88_reliability.py tests/weknora_port/test_image_processing.py tests/weknora_port/test_image_configuration.py tests/weknora_port/test_s861_api_acceptance.py tests/weknora_port/test_s862r3_40_acceptance.py -q` — initial exit1, 3 failed / 95 passed; existing execution path guard rejects Windows C: temporary fixtures. Same command with unique E: `--basetemp` — exit0, 98 passed. No execution guard relaxed.
7. `-m pytest tests/weknora_port/test_s88_reliability.py tests/weknora_port/test_image_processing.py tests/weknora_port/test_image_configuration.py tests/weknora_port/test_image_multimodal.py tests/weknora_port/test_service.py tests/weknora_port/test_service_task_cleanup.py -q` — exit0, 76 passed.
8. `git diff --check` — exit0.

Counts overlap and must not be summed. Synthetic fixtures and mocked transactions do not establish live Provider/PG acceptance. 未实现的命令不得报告通过。

## Remaining validation / integration

- Root owns ephemeral PostgreSQL execution of `test_s88_image_retry_pg.py`: set `S88_TEST_DSN` to a disposable `s88_*` database and `S88_TEST_SYSTEM_ID` to its verified actual cluster identifier. Test uses a random schema and forces complete rollback, checking original uniqueness, distinct retry identities and migration idempotence.
- Root/A own HTTP/UI integration and coordinator server-fixed markers. C owns retrieval's status-aware image metadata filtering.
- Old image task outcomes without asset path/SHA are not silently guessed from array positions. They need current version-bound asset metadata for an explicit selected retry.
- No new real Provider, Token, UNKNOWN, ECS write, production migration, source reindex, commit or publication by workflow B.

## Independent review repair: cancel versus unresolved external send

C review identified that cancellation could overwrite a processing task's state with `cancelled` while Caption/Embedding outcomes still said `sending`; a new idempotency key could then bypass the state-only retry admission guard.

- `image_tasks.py`: cancellation promotes unresolved sends to `unknown`, preserves outcomes, and returns `IMAGE_REQUEST_OUTCOME_UNKNOWN`; retry admission independently checks unresolved dispatch checkpoints, including cancelled/failed/orphaned labels. Worker reclaim shares the same predicate.
- `documents.py`: selected retry clears an embedding sending checkpoint only in the transaction that successfully switches indexes and completes the task. A crash/failure before that commit retains UNKNOWN protection.
- RED: `-m pytest tests/weknora_port/test_s88_reliability.py -q -k 'cancel_sending or without_reopening'` exit1, 5 failed / 1 passed.
- GREEN: identical command exit0, 6 passed / 14 deselected.
- Related combination: `-m pytest tests/weknora_port/test_s88_reliability.py tests/weknora_port/test_image_processing.py tests/weknora_port/test_image_configuration.py -q` exit0, 42 passed. `git diff --check` exit0.

These are deterministic synthetic regressions; no Provider/ECS/production-data operations occurred.

## Root integration follow-up

The worker-local NOT_RUN entries above are retained as execution history. Root subsequently ran both targeted PG tests in the final isolated image: exit0, 2 passed (Session settings and selected image retry/cancellation). The disposable cluster was rolled back and removed; production volumes were untouched. Two old intent fixture failures were independently reproduced on baseline f3a541 with unchanged expectations. See S8_8_IMPLEMENTATION_AND_ACCEPTANCE.md and pg-final-image.log. New business Provider calls remain0.

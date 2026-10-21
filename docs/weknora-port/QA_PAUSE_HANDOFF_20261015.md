# QA pause handoff — 2026-10-15

Owner requested: finish current work and wait for a later continuation. No further implementation or testing is authorized by this pause request.

## Worktrees and accepted boundaries

- Active QA worktree: `E:\WK\qa-entry`; base `1530af360736451a96d6faf6d2c200f2ab99f3ff` (caption language default fix).
- No production or test code was changed in this QA continuation. This pause commit contains only this handoff and the four baseline evidence files.
- Local runtime worktree: `E:\WK\model-cfg`; accepted commit `e81c17ebb910451fd3f1bb4cff13aba45d849be0`; verified clean during pause. Do not change or retest that batch merely to rebuild context.
- Main integration worktree: `E:\RAG-Worktrees\weknora-python-port-20261013`; prior recorded base `2bc2a063a11d1cf3a9e42a9ecfcd55d4124ce3df`. Its existing untracked DeepSeek handoff/Go build evidence must be preserved. No edits there in this pause.
- MQ worktree: `E:\WK\mq-grace`, previously accepted `be5b1096169ef319505bb191b31da20cf7bca516`; no changes here.
- Fixed upstream: `E:\RAG-reference\WeKnora-3e8b0bfc`, commit `3e8b0bfc80b845b2d4b2ed683994748741450a97`.

## Baseline command and observed results

Command executed in each exact worktree (main at 2bc2a06 and QA at 1530af3):

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest --noconftest tests/weknora_port/test_document_profile.py::test_source_summary_configuration_id_resolved_text_language_and_positive_limits -q -p no:cacheprovider --tb=short
```

Environment used: PYTHONDONTWRITEBYTECODE=1; PYTHONIOENCODING=utf-8; PYTHONPATH includes the existing reader/runtime dependency directories.

Both executions completed with exit code 1 (FAIL). Evidence under `evidence/QA_ENTRY_20261015/`: `profile.2bc.stdout.txt`, `profile.2bc.exitcode.txt`, `profile.153.stdout.txt`, `profile.153.exitcode.txt`.

Observed QA failure: test_document_profile.py:45 expects generate_summary_prompt to be None for custom/missing template ID with raw YAML text, but the returned value is 'YAML MUST NOT WIN'. Both baselines have the failure. This is evidence of an existing assertion/configuration discrepancy, not a new caption regression. No assertion was relaxed and no fix has been applied.

Source reading established that Go startup template resolution overwrites raw text when an ID resolves, and retains the raw text when the template cannot be found. Next work must contrast both cases against the fixed source before deciding the implementation and test correction. Do not turn the failing baseline into PASS by explanation alone.

## Pending QA scope (NOT RUN / NOT IMPLEMENTED in this continuation)

1. Same-session follow-up with different request_id: prove persisted history enters rewrite and final messages; new assistant persists through the formal HTTP entry.
2. Empty retrieval: only SearchNothing should enter source fallback handling. Source default is `fallback_strategy: model`; fixed returns configured text; model uses a separate fallback prompt plus history; empty fallback prompt falls back to fixed. Preserve cancellation/scope/privacy/budget failures as failures. Do not alter P5 thresholds/switches or auxiliary model routing.
3. Caption language deterministic causal comparison: actual provider request language and image task/ledger outcomes at 2bc2a06 versus 1530af3.
4. Source document_profile/configuration two-case comparison described above.
5. Actual Go KnowledgeQA through NewSessionService and real plugins/EventManager remains unproven. Prior GOPROXY=off missing-dependency build is a dependency failure, not a safety rejection or engine parity pass. Only precise pinned target dependency closure may be cached on D if resumed; no compiler/system installation.

## Isolated services and protections

- Existing isolated PG: `weknora-python-resume-pg-20261013`, localhost:56423, system identifier `7696088403504697382`, logical database `weknora_python_port_20261013`.
- Existing isolated Redis: `weknora-python-resume-redis-20261013`, localhost:56424; last verified run_id `fa7bde76dcd020d396902fa924d0ea3e7b26ceb2`, container `692b84d8fee031b145e6b1b0037625a955f0bb3cdb321854d11a6bbb03b02664`.
- QA native_fixture still has an old expected Redis identity and DB0. Before any new QA integration writes, independently verify current identity and use explicit DB15 with unique prefixes. Do not silently change the legacy identity, flush Redis, or run shared-PG tests concurrently.
- Existing Local identity receipt: `E:\WK\model-cfg\docs\weknora-port\evidence\LOCAL_TAKEOVER_20261015\redis-identity.json` (read-only reference).
- No services were stopped by this pause. Unrelated running Python processes have not been killed. Both baseline test commands completed, and no child agent is active.
- No real provider calls, model pull, old business DB/volume mutations, UNKNOWN release, deploy, push, tag, release, or P8 deletion were performed in this pause.

## Resume protocol

Read this handoff, verify actual HEAD/dirty and any active work, then continue the pending QA scope only when Owner resumes. Preserve the accepted Local/MQ batches for later reviewed integration. Keep SOURCE_READ / IMPLEMENTED / OFFLINE_PASS / ISOLATED_INTEGRATION_PASS / REAL_PROVIDER_PASS / CUTOVER_PASS distinct. No current new stage PASS is claimed.
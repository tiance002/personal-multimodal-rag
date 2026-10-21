# P25 — MML Remaining Samples Minimal Real Wiring

**Status:** `P25_COMPLETE`
**Date:** 2026-10-17 (Asia/Shanghai)
**Baseline / source SHA:** `9fe6f853d8dad30e97314b83984633f621940a90`
**Branch:** `codex/weknora-python-port-integrate-image-20261014`

## Outcome

All three frozen P25 message requests completed through the real FastAPI query path against the reused PostgreSQL/pgvector indexes. The task used real SiliconFlow Embedding, Rerank, and Cheap model calls; the provider boundary was observed, not mocked. `row-0959` and `row-0685` reached final Cheap generation. `row-0434` correctly stopped at the existing fixed evidence-insufficient response because Rerank retained no candidate. This is a completed wiring/quality run, not a claim that all three answers are correct.

The final outcome is **2 generated answers + 1 fixed refusal, 23,359 settled Tokens, 11 settled provider calls, 0 new UNKNOWN, 0 reserved calls**. No source-code, prompt, model, route, threshold, ingestion, or index changes were made. The earlier pre-authorization blocked checkpoint is preserved at `E:\WK\run\p25-20261017\pregrant-blocked-report.md` and `pregrant-blocked-results.json`; it is superseded by this separately authorized run, not erased.

## Runtime and scope

- API: P25 FastAPI on `127.0.0.1:56425`; allowed origin recorded as `http://127.0.0.1:56426`. The normal session, message, SSE, and history endpoints were used. This P25 task did not require a browser run.
- Worker: started with the P25 authority config and verified alive. No upload/index task was queued because the requested existing indexes were reused.
- PostgreSQL: container `eviroute-rag-postgres`, ID `89c5cb748e2ec7aca3b1e3b181dfe936a4054137ddb4de5c4e8689e86387e720`, database `weknora_python_port_20261013`, system identifier `7696088403504697382`, port `56423`.
- Redis: container `eviroute-rag-redis`, ID `15fe772fab7d9ac5b6023ff2075ebb5e12ccee80e274f91b16fe612e8748e897`, DB 15, port `56424`, run ID `dcff9d65a25725574ba0cfbc743d409fac211ef0`, unique prefix `weknora-python-p25:00a84e1963ee4b22819a00dfe2b60023`.
- Reused MinerU-B MML KB `7f1d720d-9561-446f-a8cc-6a562c339d23` for `row-0959` and `row-0434`; reused Pew KB `8363529a-0ad1-4b0d-8f2b-24c36e9cd20b` for `row-0685`. Every request had exactly one KB scope and `document_scope=[]`. The exact source files and SHA256 values are in `E:\WK\run\p25-20261017\runtime\query-plan.json`.
- Models were verified from the running API configuration and provider records: Embedding `BAAI/bge-m3`, Rerank `BAAI/bge-reranker-v2-m3`, Cheap `XingChenAGI/Xing4.0-29B`, all on SiliconFlow. Expensive/DeepSeek was not configured or called. All 3 sessions read back `rerank_threshold=0.3`, `rerank_top_k=30`, `embedding_top_k=30`, rewrite enabled, and empty document scope.

## Per-question results

| QID | Search and Rerank | Final Context and answer | Gold diagnostic | Tokens / message latency | Assessment |
|---|---|---|---|---:|---|
| `row-0959` | Search returned 30 candidates, 19 from the expected B3 document. Rerank retained 2. Raw Rerank scores: `0.606066` and `0.384914`. | 2 evidence chunks, 1,720 Context characters. Context and the actual final model request both contain `radiowaves`, `microwaves`, and `ionosphere`. Cheap answered that radio waves and microwaves are used for long-distance communication, describing ionosphere refraction and satellite links. Provider `finish_reason=stop`. | Frozen Gold: `radiowaves`, `microwaves`. Project-native diagnostic: normalized EM `0`, character F1 `0.192308`; official judge `NOT_RUN`. Manual core-fact inspection aligns with the Gold concepts, but the strict diagnostic is not a passing score. | `8,216` (`8,052` input + `164` output); `24.395 s` | Full retrieval-to-generation path reached. Public structured references were empty under the P21 citation-off behavior; internal evidence is present in the captured Context. |
| `row-0434` | Search returned 30 candidates, 28 from the expected Cat-vs-Dog document. A candidate containing `Kaggle`, `Cat vs Dog`, and `dataset` was present (chunk `085643dc-5ebb-4b8a-b7f1-75427da40015`, base search score `0.0106943`) but ranked 9/30 by Rerank at `0.0005480`. Rerank's top score was only `0.0021434`; no candidate met the session threshold `0.3` or the existing `0.15` top-1 fallback floor. | No final candidate / Context. The application returned its fixed evidence-insufficient response. Final answer model was not called. | Frozen Gold: `Cat vs Dog dataset`. Project-native diagnostic: normalized EM `0`, character F1 `0`; official judge `NOT_RUN`. | `7,257` (`7,229` input + `28` output); `4.519 s` | Search found the correct source and a relevant passage, but this query's rewritten Chinese text received extremely low Rerank scores. The normal filter/fallback behaved as implemented; no code bug was evidenced. This is a retrieval/Rerank quality failure, not an answer-model failure. |
| `row-0685` | Search returned 30 candidates from the expected Pew document. Rerank's top candidate scored `0.814809`. | 1 evidence chunk, 815 Context characters. It includes the aggregate `79%` and “no significant differences,” but does not include White- and Black-specific percentages. Cheap answered that both groups had essentially the same expectation and added an unverified prose “Source” line. The request contained the group names as part of the question, but those names/percentages were not evidence in the Context. Provider `finish_reason=stop`. | Frozen official-typed Gold `Not answerable` retained; its full-document exclusion coverage remains `UNVERIFIED_PER_P24`. Project-native diagnostic: normalized EM `0`, character F1 `0.069892`; official judge `NOT_RUN`. Do not interpret this as a verified official QA score or as proof that the source is unanswerable. | `7,886` (`7,747` input + `139` output); `8.019 s` | Evidence coverage is incomplete for the asked subgroup comparison. The generated assertion of equal group expectations is not established by the captured Context. No structured references were returned, so the answer's prose source line is not a validated citation. |

The answer metrics above are project diagnostics against the frozen native Gold, not official MMLongBench-Doc benchmark scores. The `row-0685` Gold has the unresolved P24 coverage caveat. No combined accuracy is reported across these three items.

### 0434 Rerank diagnosis

The frozen English question was rewritten to `PPT中coding time部分介绍的训练数据集是什么`. The raw Rerank request contained 30 passages. The correct-source passage containing `Kaggle`, `Cat vs Dog`, and `dataset` received `0.0005480` (rank 9); the highest candidate scored `0.0021434` and was generic training-code text. With the session's recorded threshold `0.3`, and the source implementation's top-1 fallback floor of `0.15` (`backend/weknora_port/retrieval/rerank.py`, `filtered`), no passage was accepted. The fixed refusal therefore reflects low model relevance scores under existing policy. This single-sample result does not justify changing a global threshold.

## Token ledger and authorization

- Owner-authorized P25-only grant: `P25-20261017-SF-1000000`, an additional 1,000,000 Token headroom. It was created against the then-current occupied balance of `1,669,727`, producing a temporary limit `2,669,727`.
- P25 usage: 11/11 calls settled; total `23,359` Tokens (`23,028` input, `331` output); 0 UNKNOWN and 0 reservations. Per-QID values are in the table and the session ledger readback.
- After the task-owned API and Worker were stopped, the P25 fence was closed at occupied balance `1,693,086`, leaving headroom 0. Ledger rows increased from 715 to 726; the pre-existing 61 UNKNOWN rows remain 61. No old UNKNOWN, settled row, or reservation was reset, released, or rewritten.
- Exact immutable fence receipts: `E:\WK\run\p25-20261017\fence\open-after.json` and `close-after.json`. Final ledger snapshot: `E:\WK\run\p25-20261017\runtime\snapshot_after_close.stdout.txt`.

## Harness issues and retained failures

- The first `row-0959` session creation returned HTTP 403 because the harness initially sent the wrong Origin. It failed before a message request or model send and cost 0 Tokens. The harness Origin was corrected; the one full `row-0959` message request then succeeded. The failed attempt is preserved in `runtime/results/row-0959.json` and `attempts.json`.
- After the successful `row-0959` SSE/history response, the task-side post-run ledger collector queried a nonexistent `created_at` column and exited 1. The question was not resent. The result was recovered from the already-saved SSE, history response and read-only PG ledger; `runtime/results/row-0959-complete.json` records that recovery. The API/Worker remained healthy.
- The API's SSE parser did not populate its `finish_reason` field for the two generated answers; the independent provider-boundary observation recorded `stop` for both. No answer request was repeated.
- P21 disables public citations. `knowledge_references` and chunk-readback lists are empty for these runs by that policy; evidence identity and text are retained in the internal stage/Context captures. No user-visible citation validity is claimed.
- All actual failures, including the initial 403 and the collector exception, remain in `E:\WK\run\p25-20261017`; no old P9/P11/P24/P18 outcome was rewritten.

## Commands and validation

All commands used the repository/runtime paths shown here; one-off scripts and full stdout/exit files are retained under `E:\WK\run\p25-20261017`.

| Command / action | Exit | Result |
|---|---:|---|
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\p25-20261017\p25_fence.py open` | 0 | Distinct P25 1,000,000-token fence opened; receipt `fence-open.stdout.txt`. |
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\p25-20261017\p25_runtime.py start` | 0 | Real API ready, model identities checked, Worker alive; `runtime-start.stdout.txt`. |
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\p25-20261017\p25_runtime.py one --qid row-0959` (first, pre-message 403) | 0 | Failure captured before any model send; `query-0959.stdout.txt`. |
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\p25-20261017\p25_runtime.py one --qid row-0959` (successful message, then collector error) | 1 | Message/SSE/history succeeded; only the post-run ledger collector failed. Recovered without a repeat; `query-0959-attempt02.stdout.txt`, `recover-0959.stdout.txt`. |
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\p25-20261017\p25_runtime.py one --qid row-0434` | 0 | Real retrieval/Rerank and fixed refusal captured; `query-0434.stdout.txt`. |
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\p25-20261017\p25_runtime.py one --qid row-0685` | 0 | Real retrieval/Rerank/Cheap answer captured; `query-0685.stdout.txt`. |
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\p25-20261017\analyze_p25.py` | 0 | Offline reconcile of calls, usage, answer metrics and Context terms; `analyze_p25-retry2.stdout.txt`. |
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\p25-20261017\snapshot_sessions.py` | 0 | Read-only session settings and per-session ledger readback; `snapshot_sessions.stdout.txt`. |
| Verified P25 API/Worker process identities, then stopped only PIDs `33840` and `23108` | 0 | Both exited; ports 56425/56426 are free; `runtime/stop-record.json`. PG/Redis were not stopped. |
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\p25-20261017\p25_fence.py close` | 0 | P25 fence closed; headroom 0; `fence-close.stdout.txt`. |
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\p25-20261017\snapshot_budget.py` | 0 | Final 726 rows, 61 UNKNOWN, 0 reservations; `snapshot_after_close.stdout.txt`. |
| `git diff --check -- progress.md docs/evaluation/P25_MML_MINIMAL_REAL_WIRING.md docs/evaluation/P25_MML_MINIMAL_REAL_WIRING.results.json` | 0 | Whitespace validation; no source-code changes. |
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\p25-20261017\validate_p25_complete.py` | 0 | Report and evidence invariants validated; details in `validation-complete.*`. |

No full test suite was run. Browser UI, ingestion, visual-only MML items, and official MML judge were `NOT RUN` because they are outside this minimal existing-index query scope.

## Repository state and stop

No production file changed. P25 only updates this report/results and appends its resumed completion to `progress.md`; inherited `.serena`, P24 reports, owner/evidence files and P6 logs were preserved. Working tree remains uncommitted. No commit, Push, deployment, merge, tag, re-index, or next phase was performed. The P25 API and Worker are stopped; the identified PG and Redis containers remain running and unchanged.

**Conclusion:** The existing indexed RAG path can return well-grounded evidence for `row-0959`; `row-0434` is blocked by very low Rerank relevance scores and safely refuses; `row-0685` answers beyond its incomplete subgroup evidence. The remaining issues are retrieval/model quality and unverified Gold/source coverage, not a confirmed code blocker.

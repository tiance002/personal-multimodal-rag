# S8.8 R2 Acceptance Report

**Status: `STOPPED_AFTER_APPROVED_RECOVERY_SECOND_UNKNOWN_TARGET_NOT_MET`.** The fixed 55-case run completed 51 answers. Ordinal51 stopped at QU; the Owner approved one bounded recovery excluding its retry. That recovery stopped at ordinal52 generation with another UNKNOWN. Neither call was resent. No production deployment, push, tag, or Owner acceptance is claimed.

## 1. Revision and scope

- R2 source revision: `e796932`; execution image prefix: `e6fa90`.
- Input corrections preserve all 55 original questions and all original Gold records. There are 47 corrected document scopes and three corrected KB selections (47, 48, 49); Hotpot retains full-KB scope.
- This report contains aggregate results only. It excludes raw QA, source and case IDs, internal addresses, credentials, and private contexts.

## 2. Unified R2 run and scoring

| Outcome | Cases |
|---|---:|
| HTTP-completed outputs | 51 |
| Failed UNKNOWN attempts at ordinals51/52 | 2 |
| Not run after recovery stop (53–54) | 2 |
| **Fixed denominator** | **55** |

Agent review of all 55 denominator positions found **41 core-correct, 3 core-partial, 7 core-wrong, and 4 with no result**. On the stricter full-answer dimension, review found **14 fully correct, 31 partial, 6 wrong, and 4 with no result**. These are R2 agent-review results, not owner adjudication. The core-correct target of 50/55 is **not met**.

The R2 core score is 41/55. Its numeric match with the historical same-evidence V2 Cheap plus matched-Expensive reviewed union (also 41/55) is coincidental: the cohorts and execution differ. The historical fixed Prompt v2 Cheap result remains 40/55, with 53 prior answers and two cases without a current V2 result. Neither historical result is substituted for R2, and the V2 union is not automatic-router accuracy.

The failed case 51 turn has a PostgreSQL error, `is_completed=false`, and an SSE failed event. It remains failed and is not relabeled completed. The single Provider attempt has unknown outcome: there is no response or usage record. No retry was made. This initial STOP snapshot was preserved. After the explicit recovery approval described below, only52 was submitted and the second STOP prevented53–54 and all pairs.

## 3. Usage and evidence reconciliation

| Evidence group | Attempts | Settled | Known reported tokens | Unknown attempts |
|---|---:|---:|---:|---:|
| R2 unified cohort including approved recovery | 213 | 211 | 931,628 | 2 |
| Normal UI/HTTP Quick/Smart browser proof | 8 | 8 | 19,478 | 0 |
| Isolated selected-source caption and embedding | 2 | 2 | 1,774 | 0 |
| **R2 total** | **223** | **221** | **952,880** | **2** |

All 51 HTTP-completed turns have matching native request keys, usage, wire evidence, and exported history; no completed-turn evidence is missing. Both unknown attempts are kept separate and unresolved. Their actual token use is unknown and is not treated as zero. The verified pre/post clone index fingerprint was unchanged; the isolated clone contained 117 documents, 1,826 chunks, and 1,826 embeddings. The private fingerprint itself is omitted from this public report.

The browser proof used explicit supported DeepSeek selection. Quick and Smart were exercised through ordinary UI and HTTP paths. Settings were restored through HTTP at revision 5, with prior role selections preserved. This does not certify Smart capability for the default Qwen model; that capability remains unknown and guarded.

The isolated caption task completed with `stop`. Its generated caption exposed incorrect node counts compared with independent pixel review, but the later final answer for the selected source did not repeat those incorrect counts. The underlying source-count evidence remains unresolved; no count-repair PASS is claimed. The selected caption operation changed only the clone derived evidence/index. The index was frozen after that operation and remained unchanged throughout the HTTP cohort; production was not modified.

## 4. Independent source and citation checks

- Case 18: the [official same-revision PDF](https://origin.pfultd.com/downloads/IMAGE/manual/fi-760prb/P3PC-6112-04ZH.pdf) confirms the `ctl/` route and establishes that printed footer page 21 is separate from the URL path `ctl/21`. Original Gold and historical labels are unchanged.
- Case 36: independent review confirms the full-width numeric citation is correct. An earlier ASCII-only check was a reviewer mistake and was corrected in the report review; no code change was made for it.
- These source/citation findings are reported independently from the aggregate answer scores. They do not authorize silent Gold edits.

## 5. Verification summary

| Check | Result |
|---|---|
| Targeted R2 integration selection | **PASS** — 46 passed, exit 0 |
| Affected backend selection | **PASS** — 144 passed, 1 PG skip, exit 0; mock-backed tests |
| Frontend generation controls / adapter / build | **PASS** — 13 tests / 25 tests / tsc + Vite, each exit 0; HTTP fixtures |
| Isolated real PostgreSQL scenario | **PASS** — committed function executed with unchanged assertions, exit 0; transaction rolled back |
| Frozen input contract | **PASS_OFFLINE_ONLY** — 55 inputs, 0 question changes, 47 scope changes; three KB corrections; Gold unchanged |
| Browser Quick/Smart evidence | **PASS** — 8 settled calls, 19,478 tokens, 0 usage-UNKNOWN |
| Unified R2 HTTP execution | **STOPPED** — 51 completed, two failed/unknown attempts, two not run |
| R2 core target | **NOT MET** — 41/55 versus target 50/55 |

The initial ASGI baseline reproduction had 12 passed and 2 failed. The two capability-related fixtures were subsequently made explicit without weakening unknown-capability refusal. An expanded backend selection had 188 passed, 2 pre-existing intent-contract failures and 1 skip (exit 1); those two failures were independently reproduced against the baseline and were not repaired or hidden in this task. The unified execution is real service evidence and stopped as described above; it is not a complete 55-case acceptance.

## 6. Approved recovery and final STOP

The Owner explicitly approved the frozen recovery request (SHA256 `412a6809641f72215c2006a6d168d9063fc4854444fd21ecf28337088606333f`): do not resend ordinal51; send unused52–54 once, then37 frozen same-input pairs and at most3 conditional new pairs. The 76-attempt bound was an approved scope bound, not a request to enlarge the standing token allowance.

Only ordinal52 was submitted. Its QU, Embedding and Rerank settled (6,792 reported tokens); generation surfaced `PROVIDER_HTTP_ERROR` without usable raw response/Usage evidence and became UNKNOWN. The actual upstream HTTP status/body are `MISSING_EVIDENCE`; absence of a captured response does not establish that no HTTP response reached the transport. There is no evidence of a local stream-parser defect or zero billing. Both failed turns retain their error states.

The approved recovery stopped after4 Provider attempts (3 settled,1 new UNKNOWN). Ordinals53–54 and all37 pairs remain NOT RUN. A persistent global stop marker blocks further diagnostic sends; both UNKNOWN entries remain unchanged. There is no third recovery request in this run. Post-stop DNS/TLS checks sent no HTTP request, credential, question or context; they only confirmed host-level connectivity. No new OOM occurred.

## 7. Version and next step

R2 execution source: `e796932e028e9a972fa902da4b5c5867d108c25b`; image: `sha256:e6fa90f60bc491b029e0b463624fb61891bc40d42f5647690a5d64d9ceab4c8b`. The source/index/Prompt/input contract stayed fixed through recovery. Deliver the stopped evidence and the unique remaining list; no new model batch, router adjustment, production deployment or release acceptance is claimed.

## 8. Implemented changes and command evidence

- `475e147`: image save/load recovery and stale-KB guards; read-only capability provenance through existing settings APIs; Quick/Smart and task allowance remain separate from model role and vendor capacity. No Prompt editor.
- `e796932`: original-input metadata compiler, scope-specific profile wiring and unified HTTP collector; no Gold intake in inference and no retrieval/P5/schema changes.
- `python -m pytest -q tests/test_s88_r2_input_contract.py tests/weknora_port/test_s88_r2_scopes.py tests/weknora_port/test_s88_r2_unified_acceptance.py`: exit 0, 46 passed.
- `npm.cmd --prefix frontend test -- generation-settings.spec.ts --reporter=line`: exit 0, 13 fixture-backed UI tests.
- `npm.cmd --prefix frontend test -- adapter.spec.ts --reporter=line`: exit 0, 25 fixture-backed UI tests.
- `npm.cmd --prefix frontend run build`: exit 0, TypeScript and Vite.
- The isolated image ran the committed PostgreSQL test function through `r2_pgtest.py` with assertions unchanged and skip/fail made fatal: exit 0; transaction rollback. This is a direct function run, not an installed pytest runner.
- Private recovery helpers: serial guard suite 6 passed; pair helper suite 26 passed, followed by one narrow absent-attribute check (1 passed); all 37 actual captured pairs passed a gated, transport-denied runtime check with zero sends. Counts overlap and are not a new production-quality score.

Separate pre-cohort invalid-ID HTTP 422 and zero-send preparation failures remain preserved in private evidence. They are not silently removed or treated as paid completions.

## 9. Unique unresolved list

| Ordinal(s) | First confirmed break / minimal next action |
|---|---|
| 2, 7, 12, 13, 22, 29, 45 | Generation mixes procedures, conditions, referents or conclusions despite relevant current evidence. Run the already frozen same-input Expensive pair once after STOP recovery approval; no automatic rerun or new Prompt. |
| 18 | Original task text/Gold conflicts with the original PDF URL boundary; preserve both and the separate errata proof. Do not append the page footer to the URL. |
| 47 | The immutable original PDF/text layer has the gender percentages; they are absent from the entire persisted source-text index before Search. The frozen code also reproduces this loss by numeric-line cleanup. A separate offline fix is now committed; the old index and actual answer were not rewritten. |
| 48 | The available grade percentages permit the required comparison; generation refused because unrelated bins were unreadable. Preserve the rounding/median-boundary qualification and test the same input once. |
| 51 | QU has an UNKNOWN send without usable response/usage receipts. The approved recovery excluded its retry. Preserve the failed turn and ledger. |
| 52 | Generation ended in UNKNOWN after three earlier settled calls. Whole-document visual counting remains a separate unproved capability; no completed answer exists to score. |
| 53–54 | Not sent after the second STOP. |

Ordinal 22's late correct conclusion versus unretracted opening remains a documented adjudication uncertainty. The conservative score follows the prefrozen contradiction rule; pair eligibility is unchanged. These are agent findings, not human approval.

## 10. Post-run numeric evidence fix — offline only

After the frozen real run, the Owner requested precise failure-stage details. Read-only original-PDF inspection confirmed that the required chart values exist. The frozen parser has two independent deletion gates: `_strip_arxiv_and_page_num_lines` removes unlocated 1–3 digit lines, and `_strip_chart_text_debris` can then remove numeric-only runs. This reproduces the observed labels-present/numbers-missing index shape; lack of historical parser-build/intermediate-text evidence still prevents claiming the exact old failing build.

Commit `bed0a6675700367f0bfbfd904d440fba4254d863` removes the unlocated numeric-line deletion and protects numeric-only runs while keeping the existing nonnumeric debris and arXiv guards. Synthetic RED results were 5 failures, then 2 failures after the first gate fix; the final affected pytest selection passed18 tests (exit0). Two existing PDF guard bodies executed unchanged through an AST harness also passed; this is not a full pytest-module run. An attempted broader legacy test-module collection failed because optional `pptx` was unavailable and remains recorded as BLOCKED, not PASS.

Actual saved page-text replay retains all10 chart values through the full postprocessor (11/11 numeric lines including an unlocated page number). Raw PDF, saved text and frozen index hashes were unchanged. New row/column binding, reparsing/re-embedding, and a new real-runtime quality run are **NOT RUN**. Unlocated page numbers can remain; the existing positional/page-edge guard is unchanged. The frozen real QA source remains `e796932`/image`e6fa90`; the41/55 result is not reassigned to this later code or improved using it.

Complete per-case questions, all accepted Gold answers, full actual responses and first-confirmed failure stages are delivered in the private failure report and evidence archive, never in the public repository.

## 11. Exact product-profile boundary

The real browser Quick/Smart proof used an explicitly selected supported DeepSeek model through normal configuration APIs. The55-case cohort used the existing explicit, nondefault Qwen Quick operational allowance (2048 output tokens), not certification of the default Qwen endpoint's full capacity. Default Qwen capability/capacity uncertainty remains visible and guarded. Quick/Smart did not substitute Cheap/Expensive roles automatically. This diagnostic-profile result is not a production-default acceptance pass.

The current source has only one `PROVIDER_HTTP_ERROR` creation path: rejection of a non-2xx response in `models/transport.py`, before yielding to the stream adapter. For ordinal52 this establishes the error boundary; the numeric status and vendor body were not retained in the available receipts. The vendor body is deliberately discarded by existing privacy handling. There is no basis to invent a specific402/429/5xx, a refund, zero usage, or a parser regression. A minimal follow-up would retain safe status/error metadata, without recording vendor bodies or retrying the UNKNOWN call.

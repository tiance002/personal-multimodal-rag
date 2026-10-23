# S8.8 Real Quality Progress (snapshot through 2026-10-24)

**Status: progress snapshot; final acceptance is not met.** The fixed evaluation denominator is 55 answer-Gold cases, with a target of at least 50 core-correct answers. The fixed Prompt v2 cohort has 53 completed answers and two cases without a current V2 result; the root-agent provisional core score is **40/55**. This is below target and is not owner approval.

## Reconciled provider activity

| Batch | Calls | Reported tokens | Scope/result |
|---|---:|---:|---|
| Original S8.8 batch | 13 | 40,193 | 2 Qwen prompt attempts; 9 image calls, including 2 normal-completion controls; 2 DeepSeek Quick/Smart controls; one Qwen response incomplete |
| Prompt v1 Expensive follow-up | 1 | 5,657 | Separate prompt/profile result |
| Prompt v2 selected Cheap subset | 6 | 28,395 | Six fixed-context V2 cases |
| Prompt v2 Cheap fixed-context remainder | 47 | 188,260 | Remaining V2 answer cases |
| Prompt v2 Expensive follow-ups | 17 | 73,539 | Separate model variant; not combined into V2 Cheap accuracy |
| Prompt v2 matched Expensive pairs | 2 | 14,512 | Separate model variant |
| BGE rerank baseline/candidate replay | 4 | 45,618 | Retrieval diagnostic calls, not answer generation |
| Prompt v3 Cheap subset | 4 | 18,350 | Separate prompt version |
| Prompt v3 Expensive pair | 1 | 10,989 | Separate model variant |
| HTTP39 diagnostic trace | 5 | 8,406 | Settled diagnostic trace; outside fixed V2 answer cohort |
| HTTP33 diagnostic trace | 4 | 18,869 | Settled diagnostic trace; outside fixed V2 answer cohort |
| HTTP36 formal API diagnostic | 4 | 19,999 | Settled; core PASS, citation precision FAIL; outside fixed V2 answer cohort |
| HTTP24 formal API diagnostic | 4 | 20,110 | Settled; core Gold correct, citation MISSING, year/city binding unverified; outside fixed V2 answer cohort |
| **Total** | **112** | **492,897** | **0 unresolved usage-UNKNOWN calls in this snapshot** |

The totals are reconciled from saved native result states and settled usage receipts. Partial/checkpoint mirrors are not counted as extra calls. HTTP36 and HTTP24 were added after the earlier 104-call snapshot; both now have settled native receipts and are included here. The 112 total includes reranking and diagnostic activity, so it is not a count of answer attempts.

## Fixed Prompt v2 quality

The 55-case denominator retains all cases: 53 V2 answers are complete; two have no current V2 result. The provisional root-agent adjudication counts 40 core-correct answers. Of the 53 completed answers, 13 are not core-correct; the other two cases have no current V2 result and remain unscored in the denominator. Ordinal 34 is explicitly adjudicated WRONG by the root agent and is included in the 40/55 result. Agent review is not owner approval. The success target of 50/55 has not been met.

The separate Expensive follow-ups, V3 answers, historical labels, and retrieval diagnostics are not pooled into this fixed-profile score. Correctness and unsupported-extra-fact issues remain separate review dimensions.

## Image completion and semantic review

The original batch included nine image calls: nine returned nonempty output with normal `stop` completion, including two normal-completion image controls. Independent semantic review classified four as faithful or substantially faithful, two as mixed, and three as unresolved due to formulas or dense text. One major transcription error was recorded (`IGN` for visible `IMG`). Completion status does not establish image-caption accuracy.

## Retrieval diagnostic

Four BGE calls compared baseline passage scores with source-head-prefixed candidates for two fixed questions. Offline replay through the unchanged 0.30 retrieval threshold, top-1 fallback, and MMR logic showed the URL candidate at score 0.272 (rank 3), below threshold, while another candidate scored 0.282 and was also below threshold. The tourism candidate rose from score about 0.109 (rank 18) to about 0.971 (rank 1). A separate HTTP33 review verified the tourism candidate in the actual final context; the rerank replay itself remains a retrieval-score diagnostic. This diagnostic does not score answer correctness. The reranker did not report a model identity in its result.

## Provenance and versions

Fixed answer and rerank batches use runtime source revision `01ae7cf05767e211224edfa9c0d5e0a3267f6e82` with execution image `sha256:341647bfd12c76a1cc383d30e94121e2347445f07d46579843369673f73149c6`. The rerank passage rendering used source revision `a48f29f61e7c88ceebf4bffaf9f1f1ee6ce636d7`. HTTP39 used source revision `6bd49e7d48e2ee5a52e572b094a0104f5234aff8` and execution image `sha256:62bccab7a5052bbbb5056ca0a96a0de7b29daf55380f4cf5e36f773b0a6a3652` for five calls. HTTP33, HTTP36, and HTTP24 used source revision `b5fc` and execution image digest prefix `07a9`. The current report worktree HEAD is `b132d9e`. Production was not changed. The default P5 threshold remains 0.35.

A separate read-only source proof covered all 53 captured generation inputs (458 context handles, 457 unique source rows); it caused zero provider calls and zero database writes. Historical source-version provenance for a separate 197 current-text discovery matches remains unknown.

This public snapshot omits full questions, Gold records, request contexts, case identifiers, internal source identifiers, and full answers. Detailed evidence is retained privately.

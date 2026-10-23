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
| HTTP39 Quick ordinary HTTP control | 4 | 6,551 | Same configured-model control; separate from V2 score |
| HTTP39 Smart ordinary HTTP control | 4 | 6,606 | Same configured-model control; separate from V2 score |
| Selected-caption isolated clone repair | 2 | 1,416 | Normal caption + embedding; derived index update |
| HTTP50 clone first attempt | 3 | 7,021 | Settled retrieval calls; failed before final generation |
| HTTP50 clone repaired r2b | 4 | 8,734 | Completed; core correct, overall PARTIAL; changed evidence |
| HTTP18-r3 formal API diagnostic | 4 | 16,854 | Completed; core/overall PARTIAL; precise endpoint unresolved |
| HTTP50-input standalone Expensive pair | 1 | 1,559 | Core correct / overall PARTIAL; no improvement |
| HTTP18-input standalone Expensive pair | 1 | 1,904 | Core/overall PARTIAL; no improvement |
| **Total** | **135** | **543,542** | **0 unresolved usage-UNKNOWN calls in this snapshot** |

The canonical native-receipt reconciliation v6 contains 135 unique request/capability pairs and no duplicates or usage-UNKNOWN calls. ZIP/extracted/checkpoint mirrors are counted once; process identity alone is not the deduplication key. The earlier 112 calls / 492,897 tokens plus controls 8 / 13,157, caption repair 2 / 1,416, failed HTTP50 retrieval 3 / 7,021 and completed HTTP50 4 / 8,734 give the historical 129-call / 523,225-token subtotal. Formal HTTP18-r3 adds 4 / 16,854 and the standalone pairs add 1 / 1,559 and 1 / 1,904, giving the totals above. These include diagnostics and failed answer pipelines, not 135 answers. Monetary cost remains UNPRICED. The formal HTTP18-r3 result and both standalone pairs are now included exactly once in accounting, and excluded from the fixed V2 scores.

## Fixed Prompt v2 quality

The 55-case denominator retains all cases: 53 V2 answers are complete; two have no current V2 result. The provisional root-agent adjudication counts 40 core-correct answers. Of the 53 completed answers, 13 are not core-correct; the other two cases have no current V2 result and remain unscored in the denominator. Ordinal 34 is explicitly adjudicated WRONG by the root agent and is included in the 40/55 result. Agent review is not owner approval. The success target of 50/55 has not been met.

The same-evidence V2 Cheap plus matched-Expensive reviewed union is 41/55, with one additional core rescue. This union is not measured automatic-router accuracy. Separate V3 answers, changed-context HTTP results, historical labels, and retrieval diagnostics are not pooled into either V2 score. Correctness and unsupported-extra-fact issues remain separate review dimensions.

## Image completion and semantic review

The original batch included nine image calls: nine returned nonempty output with normal `stop` completion, including two normal-completion image controls. Independent semantic review classified four as faithful or substantially faithful, two as mixed, and three as unresolved due to formulas or dense text. One major visible-text transcription error was recorded; the original text is retained privately. Completion status does not establish image-caption accuracy.

## Retrieval diagnostic

Four BGE calls compared baseline passage scores with source-head-prefixed candidates for two fixed questions. Offline replay through the unchanged 0.30 retrieval threshold, top-1 fallback, and MMR logic showed the URL candidate at score 0.272 (rank 3), below threshold, while another candidate scored 0.282 and was also below threshold. The tourism candidate rose from score about 0.109 (rank 18) to about 0.971 (rank 1). A separate HTTP33 review verified the tourism candidate in the actual final context; the rerank replay itself remains a retrieval-score diagnostic. This diagnostic does not score answer correctness. The reranker did not report a model identity in its result.

## Provenance and versions

Fixed answer and rerank batches use runtime source revision `01ae7cf05767e211224edfa9c0d5e0a3267f6e82` with execution image `sha256:341647bfd12c76a1cc383d30e94121e2347445f07d46579843369673f73149c6`. The rerank passage rendering used source revision `a48f29f61e7c88ceebf4bffaf9f1f1ee6ce636d7`. HTTP39 used source revision `6bd49e7d48e2ee5a52e572b094a0104f5234aff8` and execution image `sha256:62bccab7a5052bbbb5056ca0a96a0de7b29daf55380f4cf5e36f773b0a6a3652` for five calls. HTTP33, HTTP36, and HTTP24 used source revision `b5fc` and execution image digest prefix `07a9`. The current report worktree HEAD is `1f927e1`. Subsequent source revisions are `dd18996` (bounded original identity into final context), `6b63b19` (validated image evidence through serial strategy), and `1f927e1` (profile-only lossless metadata deduplication). Production was not changed. The default P5 threshold remains 0.35.

A separate read-only source proof covered all 53 captured generation inputs (458 context handles, 457 unique source rows); it caused zero provider calls and zero database writes. Historical source-version provenance for a separate 197 current-text discovery matches remains unknown.

This public snapshot omits full questions, Gold records, request contexts, case identifiers, internal source identifiers, and full answers. Detailed evidence is retained privately.

## Ordinary HTTP controls and isolated caption repair

The supported configured-model Quick/Smart pair used identical final messages and context, inherited saved session settings, and differed only in the thinking switch. Both retained the 2048 output cap, completed with correct core answers/current citations, and matched SSE, History and replay. Smart reported 35 reasoning tokens; the absent Quick reasoning counter remains UNKNOWN. Each run had four provider observations matching native usage, with null input/output. This was an explicit EXPENSIVE_ONLY/OFFLINE_ARM control, not DYNAMIC automatic escalation or Qwen Smart capability certification. One question does not establish a quality advantage.

The selected source image was processed by the normal caption/embedding/index-switch path in a physical clone retaining the whole original authorized KB (four documents and 267 original chunks). One derived image caption was added; original text and production were unchanged. The subsequent HTTP50 r2b answer had a correct core layer count but repeated inaccurate node-count extras from the caption, so overall quality is PARTIAL. The citation resolves to the current derived caption, not original text. Native stop/DONE and completed history are present. The raw-capture complete=false flag records that the consumer stopped at semantic DONE before natural iterator EOF; it does not denote a failed business completion. This Prompt v3 changed-evidence result is excluded from both V2 scores.

## Follow-up fixes and existing offline evidence

The scoped image handoff fix preserved existing scope, current-version and source-materialization guards. Its synthetic RED was 5 failed / 13 passed; the affected strategy tests finished with 65 passed, exit 0. A separate actual three-row replay reproduced the old RetrievalScopeError and passed the repaired path through generation preparation, retaining generated-caption classification. No new Provider or DB call was made for replay.

The profile snapshot fix preserves full metadata in SHA-addressed sidecars and supports explicit lossless hydration, while retaining legacy direct-snapshot behavior. The actual 60,191,721-byte frozen profile became a 247,357-byte profile plus 40 full metadata blobs totaling 6,911,742 bytes: 7,159,099 bytes for the first snapshot, about 88.1% less. Subsequent identical snapshots add no blobs. The entire restored state equals the original. No evidence was truncated and no provider wire changed; archives must retain sidecars. This is a local replay measurement, not a measured ECS speedup.

Snapshot tests began with 5 failures; the final targeted selection passed 17 with 2 deselected, exit 0. The two excluded ASGI fixtures reproduced MODEL_GENERATION_CAPACITY_UNKNOWN with the baseline serializer and remain recorded failures. Exact commands and acceptance boundaries are in [the implementation report](S8_8_IMPLEMENTATION_AND_ACCEPTANCE.md). These product tests were not rerun for this documentation update.

## Final formal HTTP18 result and standalone model pairs

HTTP18-r3 completed in 6.90365 seconds with four settled calls / 16,854 reported tokens and zero UNKNOWN. SSE, History and the canonical port_turns record are completed; all four PG provider records match the native ledger. Its source-grounded base route is incomplete against the fixed target endpoint: the suffix was absent from the actual delivered context as well as the answer. Core and overall labels remain PARTIAL. Do not infer a suffix or silently change Gold. The raw legacy messages.turn_status remains pending while is_completed is true; history explicitly projects completed, and the final transaction updates canonical port_turns. This is existing completion semantics, not a persistence failure.

Two standalone same-input Expensive generations then completed with stop. They reused the corresponding actual HTTP messages/context but were not new full HTTP pipelines. Ordinal 50 consumed 1,559 tokens (1,506 input + 53 output), retained the correct core count and the inaccurate caption node-count extras: overall PARTIAL, no improvement. Ordinal 18 consumed 1,904 tokens (1,714 input + 190 output), retained the base route and described missing page detail: core/overall PARTIAL, no improvement. The latter does not resolve the source/Gold endpoint disagreement.

HTTP18-r3 and its standalone pair used source 1f927e1 / image prefix 256856. The ordinal 50 standalone pair used source 6b63b19 / image prefix a6ac. Neither pair is pooled into fixed V2 accuracy. The two baseline ASGI fixture failures remain recorded; these completed experiments do not turn those tests into PASS.

## Remaining work

Current calls are complete; no further current-batch calls are planned. Owner scope/input clarification and final coherent 55-case acceptance remain incomplete. Fixed V2 Cheap stays 40/55, same-evidence V2 reviewed union stays 41/55, and the target remains unmet. The unresolved source and caption issues are retained, not hidden by completion status.

## Private delivery and resource closeout

Public source/report checks and private packaging are complete. The private-only archive `S8_8_PRIVATE_EVIDENCE_FINAL_R3_1.zip` is 110,444,370 bytes, SHA-256 `a081265123619f14d274d0bafb81999530835128a76ffee8ced783e54d726667`. ZIP CRC, every member size/hash, alias restoration and HTTP18 metadata hydration passed. It preserves 55 original target identities, 53 saved original final-answer/request pairs and two explicit missing pairs; no missing answer was invented. It contains the original QA, Gold, actual contexts, requests, images and batch evidence, excluding credentials, environment files, grant authority and database data directories. It is not published to GitHub.

No Provider calls are in flight. Temporary APIs, runners and the isolated caption database are stopped; their results and volumes remain. Production is unchanged. This closes this batch's evidence delivery, while the 50-of-55 quality target and scope/input clarification remain open.

# Public Benchmark Fusion and Context Diagnostic

## 1. Scope and status

This is an M0, zero-model, zero-reindex diagnostic over the three complete development runs already on disk. It uses the saved `cases.jsonl`, manifests, source files, and the production retrieval/context code at Git SHA `241d2c7ebdc4911e92a18bdc1e48307d85c87503`.

No locked split was read for calculation, no retrieval was run, no database was opened, no corpus embedding or index was created, no download occurred, no Qwen/Judge/Ragas call occurred, and no ECS action occurred. Production defaults were not changed. The previously stopped full grid remains stopped.

The reproducibility script is kept in the ignored planning directory:
`.planning/2026-09-28-public-benchmark-fusion-context-diagnost/offline_m0.py`.
It writes only the ignored `m0-results.json` in that directory and does not contain question text.

## 2. Inputs and identities

| Dataset | Run | qids | qrels unit | dataset_hash | config_hash | index_hash |
|---|---|---:|---|---|---|---|
| SciFact | `20260928T060742Z-6f0c61007b` | 809 | official document qrels | `8608aa67e02a307745506d92316ebd0d1b2079226a576446c2bb5943e064f59f` | `404c2a1abe5ec40c9f797b6df0292171e604420caecaffa3e4e68da32cf1e223` | `f6c8b191834638dfa8315adc158b1875ac69c0bef5ba2baac598734976aadb93` |
| MIRACL-ZH | `20260928T071014Z-cffac0cd6a` | 328 | judged passage qrels in the fixed 6,000-passage pool | `105dde269ea99ddee8ad0ebf8649631e88c3710be9ad2b54b4ced59e4a17ad29` | same | `629b25a5a14ea2ab960fa874c3b16e8cc77a61d0bb65e0e0847ce3c04d473c82` |
| LongBench-ZH | `20260928T074111Z-442ee43dd3` | 320 | one paired source per task, diagnostic only | `d5f18bf83d652664d6778d1fab2eba0c1d6ef7781cc4c106e96217eca1cac0c1` | same | `0d41031e54185d2ebc026cdeb62a099427f35a09b0b2d2034a5ce12e4dd3c179` |

All 12 referenced source files matched their recorded SHA. The embedding and chat model identities recorded by the completed-run manifest are respectively BGE-M3 digest `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab` and Qwen digest `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`; neither model was called in this diagnostic.

### Effective configurations and observed ranking depth

The full development run used a seven-variant configuration bundle. All variants use chunk size 1,200 and overlap 120. The baseline is `candidate_k=32, rrf_k=60, top_k=5`; the saved top-k sweep uses that same retrieval configuration with `top_k=3/5/8/10`. Selected variants are:

| Dataset | Selected variant | candidate_k | rrf_k | top_k | context budget |
|---|---|---:|---:|---:|---:|
| SciFact | `rrf-30` | 32 | 30 | 5 | 8,000 chars |
| MIRACL-ZH | `candidate-k-16` | 16 | 60 | 5 | 8,000 chars |
| LongBench-ZH | `candidate-k-64` | 64 | 60 | 5 | 8,000 chars |

The following are observed raw chunk-ranking lengths for each selected variant (`min / median / p95 / max`). They show the actual saved trace depth, including empty keyword results where present; they are not the number of distinct documents used by the metrics.

| Dataset / selected variant | keyword chunks | vector chunks | hybrid chunks |
|---|---:|---:|---:|
| SciFact / `rrf-30` | 32 / 32 / 32 / 32 | 32 / 32 / 32 / 32 | 43 / 62 / 64 / 64 |
| MIRACL-ZH / `candidate-k-16` | 0 / 16 / 16 / 16 | 16 / 16 / 16 / 16 | 16 / 27 / 31 / 32 |
| LongBench-ZH / `candidate-k-64` | 0 / 64 / 64 / 64 | 64 / 64 / 64 / 64 | 64 / 111 / 125 / 128 |

Metrics use the same qid denominator within each dataset and the same folded document/passage unit across captured stages. They are not pooled across datasets. Retrieval-stage lists and RRF are chunk-ranked; nDCG@10 is calculated after first-occurrence folding to unique source IDs, and context uses the first `top_k` chunks before the same source folding. Candidate lists can be shorter than their configured cap when the source returns fewer matches.

## 3. What is and is not an independent retrieval comparison

The saved `keyword` and `vector` lists are the two candidate stages captured during one production `HybridRetriever.retrieve` call. `hybrid` is the RRF result from those lists. They are therefore observational stage traces, not independently disabled keyword-only and vector-only production runs.

RRF was replayed from saved chunk IDs using the production formula and deterministic chunk-ID tie break. Every qid in every saved variant matched exactly: 809 x 7 for SciFact, 328 x 7 for MIRACL, and 320 x 7 for LongBench. Saved nDCG@10 was also recomputed for every qid and variant.

The ranking is chunk-level. Metrics and context attribution fold the first occurrence of each chunk's source ID to document/passage level. Thus a document rank greater than `top_k=5` proves that its first source occurrence is beyond the first five chunks, but a rank at most five cannot distinguish budget rejection from repeated source occupancy when selected chunk IDs were not saved.

## 4. Baseline stage differences

| Dataset | vector mean nDCG@10 | hybrid mean nDCG@10 | vector better / hybrid better / tie | vector hit, hybrid miss at @10 | positive best rank demoted / promoted when both hit |
|---|---:|---:|---:|---:|---:|
| SciFact | 0.708971 | 0.579188 | 367 / 65 / 377 | 33 | 320 / 54 |
| MIRACL-ZH | 0.909327 | 0.644756 | 212 / 23 / 93 | 24 | 140 / 16 |
| LongBench-ZH | 0.857019 | 0.847535 | 31 / 25 / 264 | 2 | 29 / 20 |

These values do not establish a vector-only production mode advantage. They show that the captured keyword candidates materially affect the observed RRF ranking. Keyword-only folded documents were mostly unjudged: SciFact 22,670, MIRACL 6,806 unjudged plus 9 explicit grade-zero, and LongBench 4 paired positives plus 3,871 non-paired diagnostic sources.

## 5. Context attribution

The selected development variants are SciFact `rrf-30`, MIRACL `candidate-k-16`, and LongBench `candidate-k-64`, all with `top_k=5` and an 8,000-character budget.

The prior report's counts `157 / 44 / 22` mean a hybrid positive was present but the context contained **zero** positive sources. The broader offline count below counts a qid when at least one positive source present in fusion is absent from context; it also includes partial multi-positive coverage. In the earlier case-analysis wording, “multi-positive context did not cover all” counts 60 SciFact qids because it includes both zero-positive contexts and partial contexts; the strict “some positive sources present and some absent” count here is 40. MIRACL's corresponding counts are both 71; LongBench has one paired source per qid and no partial category.

| Dataset / selected variant | hybrid hit, context has zero positives | any positive source in fusion but absent from context | partial positive coverage (some present, some absent) | positive sources absent from fusion |
|---|---:|---:|---:|---:|
| SciFact / rrf-30 | 157 / 809 | 193 / 809 | 40 qids | 95 positive source records |
| MIRACL-ZH / candidate-k-16 | 44 / 328 | 115 / 328 | 71 qids | 0 |
| LongBench-ZH / candidate-k-64 | 22 / 320 | 22 / 320 | 0 | 2 positive source records |

The SciFact partial-coverage count includes four qids whose omitted positive source was already absent from the fusion candidate list. Consequently, 157 complete misses plus 40 partial-coverage qids do not equal the 193 qids with a fusion-hit source omitted by context. MIRACL's 44 complete misses plus 71 partial-coverage qids do equal 115 because every judged positive appears somewhere in its fused candidate list.

For positive source records missed by context, the selected-trace attribution is:

| Dataset | positive records missed | rank > top_k, confirmed depth exclusion | rank <= top_k, budget or repeated-chunk cause unknown |
|---|---:|---:|---:|
| SciFact / rrf-30 | 217 | 210 | 7 |
| MIRACL-ZH / candidate-k-16 | 137 | 137 | 0 |
| LongBench-ZH / candidate-k-64 | 22 | 21 | 1 |

The non-base selected variants do not store selected chunk IDs, content lengths, or a chunk-to-source map. The rank-at-most-five cases are therefore `INSUFFICIENT_TRACE`; they are not attributed to the character budget.

For the saved base variant, the ContextBuilder budget check is direct: zero qids in all three datasets rejected any of the first five input chunks. Input and selected chunk counts were identical for all modes. Base hybrid context character median/p95 were SciFact 5,336/5,763, MIRACL 1,250.5/2,109, and LongBench 5,275.5/5,887. This makes top-k depth and source occupancy the supported baseline explanations; a selected non-base budget claim requires a future trace that preserves the missing fields.

Replaying the already saved top-k variants keeps the baseline `candidate_k=32, rrf_k=60` chunk ranking identical on every qid and changes only context depth. This sweep is separate from MIRACL's selected `candidate_k=16` context; its top-5 value therefore uses the c32 baseline and is not the selected-variant count above:

| Dataset | context source recall top-3 / top-5 / top-8 / top-10 | hybrid-hit/context-miss qids at top-3 / top-5 / top-8 / top-10 |
|---|---:|---:|
| SciFact | 0.6083 / 0.6877 / 0.7361 / 0.7447 | 257 / 193 / 153 / 145 |
| MIRACL-ZH | 0.5749 / 0.7122 / 0.8405 / 0.8883 | 179 / 128 / 73 / 54 |
| LongBench-ZH | 0.8719 / 0.9188 / 0.9406 / 0.9406 | 36 / 21 / 14 / 14 |

This is offline replay of saved context traces, not a new retrieval run.

## 6. MIRACL candidate-k-16

The candidate-k-16 keyword and vector chunk lists are exactly the first 16 entries of the k-32 lists for all 328 qids. At nDCG@10 it gains on 90 qids, loses on 34, and ties on 204. No positive vector document was removed by the k-16 prefix. On all 90 gain qids, vector positive documents remained; 90 also removed unjudged keyword documents and 36 removed at least one explicitly judged-zero keyword document. Across all qids, removed keyword documents were 4,851 unjudged, 158 explicit-zero, and 54 positive.

This supports the hypothesis that pruning weak keyword tail candidates often helps, but it does not prove that noise removal is the sole cause. The 54 removed positive keyword documents and 34 loss qids show the trade-off. MIRACL context also has source concentration: 247/328 queries have at least two of five passage slots from one parent, with 527 excess repeated-parent slots and 3.393 distinct parents per query on average. This is a passage diversity signal, not proof of near-duplicate text or an incorrect merge.

## 7. QID-level cases

The following cases retain qid and case ID but no question or answer text. “Selected context miss” uses the selected development variant. LongBench cases are paired-source diagnostics only.

| Dataset | qid / case_id | evidence | diagnosis |
|---|---|---|---|
| SciFact | `1036` / `1ed6aa1ead4b` | vector gold rank 6, hybrid rank 12; selected context miss at rank >5 | vector candidate is demoted by observed fusion and then truncated |
| SciFact | `1040` / `a455148c571f` | two positives; selected best rank 6; both context positives missed at rank >5; selected nDCG delta -0.01879 | multi-positive loss after fusion/top-k; exact chunk cause unavailable for non-base trace |
| SciFact | `1234` / `bf520870f2d9` | selected hybrid gold rank 5 but context has zero; rank <=5 | budget versus repeated chunk occupancy is `INSUFFICIENT_TRACE` |
| SciFact | `752` / `001e5f68534b` | selected rank 1 and context contains the positive | normal control |
| MIRACL-ZH | `1022676#0` / `c26d0ce94540` | vector rank 3, selected k-16 hybrid rank 11, context miss; k-16 delta 0 | fusion demotion remains after candidate pruning; 13 unjudged and 3 grade-zero keyword docs removed |
| MIRACL-ZH | `855039#0` / `9b888acb2b90` | k-16 nDCG loss -0.38685; selected rank 11 versus base rank 5; context miss | k-16 is not uniformly beneficial; one positive keyword doc was removed |
| MIRACL-ZH | `1059962#0` / `9b696d65f270` | k-16 gain +0.63093; base hybrid rank 11, k-16 rank 2; context covers positive | consistent with pruning 16 unjudged keyword tail docs |
| MIRACL-ZH | `1099346#0` / `dcbdcbd546c3` | k-16 loss -0.36907 despite vector positive retained; one positive, two grade-zero, 13 unjudged keyword docs removed | candidate pruning changes RRF tail and can hurt |
| MIRACL-ZH | `1205173#0` / `001bd4ab1c9c` | k-16 tie; positive retained and context covers it | normal control |
| LongBench-ZH | `multifieldqa_zh:87bc3d04c6f924a4b27560e78f8b446e19ec779ffaba1524` / `bc9e728dcda9` | vector rank 6, base hybrid rank 12, selected rank 6; context miss at rank >5 | selected fusion recovers the paired source, but top-k drops it |
| LongBench-ZH | `multifieldqa_zh:198b2a1122828dd5539b9c9baf7b36849ecdeff4f804b2e6` / `671b6178b1e6` | vector rank 1, base hybrid rank 6, selected rank 13; context miss at rank >5 | fusion demotion plus top-k truncation |
| LongBench-ZH | `multifieldqa_zh:58fb54927d4416aabbbe68479eae60d25cac42c9cc7ce869` / `f188df9c4e77` | selected rank 5 but context miss; rank <=5 | budget versus repeated chunk occupancy is `INSUFFICIENT_TRACE` |
| LongBench-ZH | `dureader:91b4e9d1a4d0afdc7c5503c767797fd950169b9b38851c88` / `0006aa90e048` | vector, hybrid, selected rank 1; context covers paired source | normal control |

## 8. Command, runtime, and resource evidence

Command executed from the evaluation worktree:

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B '.planning\2026-09-28-public-benchmark-fusion-context-diagnost\offline_m0.py'
```

Exit code: `0`. The second run completed in about 3.73 seconds and reported `SOURCE_HASHES {'unique_paths': 12, 'verified': 12, 'mismatches': []}`. It reported exact RRF and metric replay for all 1,457 qids across seven variants each.

At the final M0 snapshot on 2026-09-28 21:13 +08:00, no benchmark Python process was running, Ollama had no loaded model, GPU utilization was 0% with 0 MiB used, CPU average was 0%, RAM was 13.3 GB free of 31.7 GB, and free disk was C: 25.7 GB, D: 165.6 GB, E: 53.7 GB. Existing Docker/Postgres processes were observed but not touched; the diagnostic made no database connection.

A separate read-only snapshot on 2026-09-29 10:35 +08:00 again found zero benchmark Python processes. `ollama ps` returned no loaded models; no model inference call was made. CPU was 3%, RAM was 15.2 GB free of 31.7 GB, GPU utilization was 0% with 8 MiB of 8,188 MiB used, and free disk was C: 25.2 GB, D: 165.4 GB, E: 57.4 GB. The Docker API was unavailable at this snapshot, so container status was not verifiable. No database or benchmark process/service state was altered for this report.

Not run by design: tests, retrieval, query embedding (actual query embeddings: 0), corpus embedding (new corpus embeddings: 0), index creation (new indexes: 0), database writes, downloads, locked evaluation, Qwen calls (0), Judge, Ragas, deployment, synchronization, and production configuration changes.

## 9. Conclusions and bounded next options

M0 is sufficient to explain the present deviation without another expensive run. The strongest supported findings are:

1. The captured keyword stage can move relevant vector hits down in RRF; the comparison is not an independent vector-only experiment.
2. Baseline top-k depth is a measurable context bottleneck. Increasing saved top-k from 5 to 8 or 10 improves source recall while leaving chunk rankings unchanged.
3. MIRACL k-16 often removes unjudged or grade-zero keyword tail candidates, but it also removes positive keyword candidates and loses 34 qids; it is a bounded candidate, not a proven universal fix.
4. Character-budget causality is unproven for selected non-base variants because the required selected chunk trace was not persisted.

At most two opt-in follow-ups are justified:

- Add trace fields for every variant: selected chunk IDs, rendered lengths, rejection reason, and chunk-to-source mapping. This is the minimum needed to separate top-k, budget, and repeated-source effects.
- If a real experiment is later authorized, compare a true vector-only path, current hybrid, and at most one evidence-backed fusion change on a fixed small development qid list using a verified read-only existing index. Do not select from locked data; do not start the stopped full grid.

No default parameter was changed and no formal production recommendation is made by this report.

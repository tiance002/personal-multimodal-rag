# V1 Reference Retrieval Sanity

Date: 2026-09-30

## Scope and result

This report summarizes the completed, fixed 24-QID Development sanity run from the saved results and manifest. It does not rerun retrieval, embeddings, database work, or models. There are eight QIDs per dataset. No raw question or answer/gold text, database connection detail, or credentials are included.

The recorded run status is COMPLETED, command exit code 0, and gate PASS. The gate is narrow: on these fixed 24 Development QIDs, Adaptive adds no hit-at-5 harm versus Vector and its mean Document Recall@5 does not decrease. It does not establish broad semantic answer quality or a generally optimal routing policy.

## Provenance and execution evidence

| Artifact / identity | Value |
|---|---|
| results.json SHA-256 | fa550862bcf15d959a6be13d955c3cd2fc1dde2650b4cea356ca2c46a2fc1a35 |
| manifest.json SHA-256 | c9264d20df4fd48b8f645e0967552a96a9b8ff8e79c607aeea8c8f933ce00e72 |
| Frozen 24-QID fixture SHA-256 | ca691e3c8b3eacacc75f1d560515d266e22160dc1de47b8101c89f5063615a2d |
| Preregistration SHA-256 | c9264d20df4fd48b8f645e0967552a96a9b8ff8e79c607aeea8c8f933ce00e72 |
| Source code Git SHA | 0b68385f237e9560ef6929297d459973a4def89d |
| Postflight | IDENTITY_UNCHANGED |
| Recorded elapsed time | 155.612 s, including full index preflight and postflight |

Recorded runner command:

    & 'E:\RAG quention\.venv\Scripts\python.exe' -m scripts.validate_reference_retrieval --output var\reports\reference-sanity-20260930

Recorded command exit code: 0. The result artifact reports 48 retrieval calls, 24 query-embedding provider calls, zero corpus embedding calls, zero database writes, zero LLM calls, and zero Locked calls.

The saved cache counters are run-scoped and in-memory. Each dataset has 16 embedding requests, 8 provider calls, 8 cache hits, and 8 unique query keys; across all three datasets this is 48 requests, 24 provider calls, and 24 hits. Raw query retention is false. Cache counters are dataset-level in the artifact and are not attributed to individual arms.

## Arm definitions

| Arm | Meaning in this report |
|---|---|
| Vector | New live vector-only arm, measured in this run. |
| OldHybrid | Historical Round 1 saved arm; reused without executing it again. Its reported timing is historical. |
| ReferenceHybrid | New fixed Reference profile, measured in this run. |
| Adaptive | Offline per-QID selection between this batch’s Vector and ReferenceHybrid outputs; it makes no new retrieval call. Its latency is a counterfactual selected-arm timing and excludes router overhead. |

The fixed Reference profile is WeKnora rank RRF with vector weight 0.7, keyword weight 0.3, RRF k=60, candidate_k=32, and top_k=5. Query expansion, query rewrite, reranking, MMR, and cloud fallback are disabled.

## Per-dataset retrieval metrics

Recall@5 is document-level. Rescue/harm counts compare each arm to the Vector hit-at-5 flag; Vector has no comparison count.

| Dataset | Arm | Recall@5 | MRR@5 | nDCG@10 | Context Recall@5 | Rescue | Harm |
|---|---|---:|---:|---:|---:|---:|---:|
| SciFact | Vector | 0.625000 | 0.525000 | 0.590023 | 0.500000 | — | — |
| SciFact | OldHybrid | 0.500000 | 0.375000 | 0.407732 | 0.500000 | 0 | 1 |
| SciFact | ReferenceHybrid | 0.500000 | 0.500000 | 0.573762 | 0.500000 | 0 | 1 |
| SciFact | Adaptive | 0.625000 | 0.525000 | 0.590023 | 0.500000 | 0 | 0 |
| MIRACL Chinese | Vector | 1.000000 | 0.854167 | 0.882118 | 1.000000 | — | — |
| MIRACL Chinese | OldHybrid | 0.625000 | 0.500000 | 0.647698 | 0.625000 | 0 | 3 |
| MIRACL Chinese | ReferenceHybrid | 0.625000 | 0.562500 | 0.688518 | 0.625000 | 0 | 3 |
| MIRACL Chinese | Adaptive | 1.000000 | 0.854167 | 0.882118 | 1.000000 | 0 | 0 |
| LongBench Chinese | Vector | 0.750000 | 0.395833 | 0.522102 | 0.750000 | — | — |
| LongBench Chinese | OldHybrid | 0.500000 | 0.229167 | 0.415261 | 0.500000 | 1 | 3 |
| LongBench Chinese | ReferenceHybrid | 0.750000 | 0.302083 | 0.450937 | 0.625000 | 1 | 1 |
| LongBench Chinese | Adaptive | 0.750000 | 0.395833 | 0.522102 | 0.750000 | 0 | 0 |

## Saved timing by arm

Retrieval times are milliseconds. Vector and ReferenceHybrid are measurements from the new run; OldHybrid is reused historical timing; Adaptive is an offline selected-arm timing with no router overhead. Context-build time is shown as mean / p95. Timing comparisons use only eight QIDs per dataset and mix cache/cold-warm effects.

| Dataset | Arm | Retrieval mean / p50 / p95 | Context-build mean / p95 |
|---|---|---:|---:|
| SciFact | Vector | 1124.952 / 178.170 / 5197.781 | 1.342 / 1.889 |
| SciFact | OldHybrid | 393.932 / 402.166 / 454.891 | 0.962 / 1.226 |
| SciFact | ReferenceHybrid | 493.473 / 352.734 / 1131.463 | 1.236 / 1.649 |
| SciFact | Adaptive | 1144.387 / 200.515 / 5229.571 | 1.368 / 1.889 |
| MIRACL Chinese | Vector | 122.863 / 71.468 / 227.000 | 1.321 / 1.738 |
| MIRACL Chinese | OldHybrid | 138.602 / 144.050 / 203.121 | 0.877 / 1.119 |
| MIRACL Chinese | ReferenceHybrid | 316.748 / 189.420 / 997.294 | 1.261 / 2.049 |
| MIRACL Chinese | Adaptive | 122.863 / 71.468 / 227.000 | 1.321 / 1.738 |
| LongBench Chinese | Vector | 140.206 / 169.482 / 212.284 | 0.878 / 1.136 |
| LongBench Chinese | OldHybrid | 586.276 / 512.725 / 1080.782 | 0.566 / 0.663 |
| LongBench Chinese | ReferenceHybrid | 875.117 / 919.429 / 1245.853 | 0.832 / 1.498 |
| LongBench Chinese | Adaptive | 140.206 / 169.482 / 212.284 | 0.878 / 1.136 |

Adaptive p50 versus Vector is 178.170 to 200.515 ms (+12.5%) on SciFact; MIRACL and LongBench p50 are unchanged because all sampled QIDs there selected Vector. This fixed-sample observation does not include actual router overhead and is not a general latency claim.

## Gate coverage and limitations

- Adaptive Recall@5, MRR@5, nDCG@10, and Context Recall@5 equal Vector’s dataset means for all three datasets. Adaptive rescue/harm counts are zero for each dataset, and Recall@5 delta is zero.
- Adaptive selected ReferenceHybrid for one SciFact QID and Vector for the other 23 QIDs. Identifier routing has therefore been exercised on only one QID in this sample.
- Static ReferenceHybrid reduced Recall@5 versus Vector on SciFact (0.625 to 0.500) and MIRACL (1.000 to 0.625). LongBench Recall@5 stayed at 0.750, but MRR@5 fell from 0.395833 to 0.302083 and Context Recall@5 fell from 0.750 to 0.625.
- Gate PASS applies only to the fixed 24-QID Development sample and its explicit Adaptive-versus-Vector checks. It does not demonstrate broad semantic answer or citation quality, performance over the full Development population, or general optimality.
- No LLM calls were made. This run therefore provides no generated-answer, citation, or semantic-support evaluation.

No additional evaluation was run while preparing this report.

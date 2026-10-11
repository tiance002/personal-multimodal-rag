# Public dataset RAG benchmark report

Generated at 2026-09-28T11:27:35.084087+00:00.

## Scope and execution

The benchmark uses the production ingestion, chunking, BGE-M3 embeddings, isolated PostgreSQL/pgvector indexes, keyword/vector/hybrid retrieval, context building, and citation readback path. Raw datasets and per-question outputs stay in the external data root. No production RAG defaults were changed and no RAG product release was deployed.

| Dataset | Evidence |
|---|---|
| SciFact | Official document-level BEIR qrels; span-level recall is unavailable. |
| MIRACL Chinese | Fixed 6,000-document candidate pool from official shard 0; results apply only to this pool. |
| LongBench Chinese | Context-to-question links are diagnostic only and are not official retrieval qrels. |

## scifact

- Dataset version: `beir-scifact-5f7d1de60b170fc8027bb7898e2efca1`
- Indexed document pool: 5183
- Cases: development 809; locked holdout 300
- Qrels semantics: `official_document_qrels`
- Source terms: {"abstracts": "ODC-By 1.0", "labels": "CC BY 4.0"}

| Phase | Split | Profile | Documents | Queries | Variant | nDCG@10 | Recall@5 | Context recall@5 | Retrieval ms | Git SHA |
|---|---|---:|---:|---:|---|---:|---:|---:|---:|---|
| baseline | development | smoke | 50 | 12 | baseline | 0.8304 | 0.8333 | 0.8333 | 85.57 | `42e4464f6caaebcb10952079a9cf859f4e943356` |
| optimize | development | standard | 5183 | 809 | baseline-t5-c32-r60 | 0.5792 | 0.6932 | 0.6877 | 378.54 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 5183 | 809 | candidate-k-16 | 0.5753 | 0.6999 | 0.6956 | 377.07 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 5183 | 809 | candidate-k-64 | 0.5643 | 0.6638 | 0.6603 | 379.15 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 5183 | 809 | rrf-30 | 0.5799 | 0.6926 | 0.6870 | 379.37 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 5183 | 809 | top-k-10 | 0.5792 | 0.6932 | 0.6957 | 388.05 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 5183 | 809 | top-k-3 | 0.5792 | 0.6932 | 0.6083 | 376.66 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 5183 | 809 | top-k-8 | 0.5792 | 0.6932 | 0.6932 | 382.96 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| validate | locked_holdout | standard | 5183 | 300 | rrf-30 | 0.5730 | 0.6719 | 0.6644 | 462.17 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |

Qwen QA: NOT RUN

- Development candidate: `rrf-30` (locked_validation_completed)
- Selection metric: `hybrid.document_metrics_at_10.ndcg_at_k`
- Effective configuration: `{"candidate_k": 32, "chunk_overlap": 120, "chunk_size": 1200, "context_budget_chars": 8000, "rrf_k": 30, "top_k": 5}`
- Paired development cases: 809; mean delta 0.0007; 95% bootstrap CI `[-4.871280983760172e-06, 0.0018911055915475487]` (2000 resamples).
- Locked holdout: COMPLETED once; run `20260928T083231Z-efc760238e`; SHA `241d2c7ebdc4911e92a18bdc1e48307d85c87503`.

- Sanitized server sync: SANITIZED_BUNDLE_READY; transfer NOT_RUN_REMOTE_SHA_CHECK_PENDING; bundle SHA `8ab649688ad85daf8c31de3f010bd4bd48b9bc6aec59634fcfd304bf2f9f7cce`.

## miracl-zh

- Dataset version: `MIRACL-ZH-CANDIDATE-POOL-SHARD0-SEED20260928-V1`
- Indexed document pool: 6000
- Cases: development 328; locked holdout 99
- Qrels semantics: `official_judgments_with_candidate_pool_scope`
- Candidate pool ID: `MIRACL-ZH-CANDIDATE-POOL-SHARD0-SEED20260928-V1`; documents 6000; seed 20260928
- Incomplete-positive exclusions: train 984; dev 294
- Source terms: {"dataset_card": "Apache-2.0", "underlying_corpus": "Wikipedia source passages; retain source attribution and upstream terms"}

| Phase | Split | Profile | Documents | Queries | Variant | nDCG@10 | Recall@5 | Context recall@5 | Retrieval ms | Git SHA |
|---|---|---:|---:|---:|---|---:|---:|---:|---:|---|
| baseline | development | smoke | 50 | 9 | baseline | 0.7581 | 0.9074 | 0.9074 | 107.11 | `42e4464f6caaebcb10952079a9cf859f4e943356` |
| optimize | development | standard | 6000 | 328 | baseline-t5-c32-r60 | 0.6448 | 0.7122 | 0.7122 | 186.63 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 6000 | 328 | candidate-k-16 | 0.6904 | 0.7673 | 0.7673 | 187.18 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 6000 | 328 | candidate-k-64 | 0.6314 | 0.7082 | 0.7082 | 189.27 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 6000 | 328 | rrf-30 | 0.6480 | 0.7224 | 0.7224 | 188.41 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 6000 | 328 | top-k-10 | 0.6448 | 0.7122 | 0.7122 | 196.96 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 6000 | 328 | top-k-3 | 0.6448 | 0.7122 | 0.5749 | 184.25 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 6000 | 328 | top-k-8 | 0.6448 | 0.7122 | 0.7122 | 192.12 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| validate | locked_holdout | standard | 6000 | 99 | candidate-k-16 | 0.6761 | 0.7563 | 0.7563 | 219.42 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |

Qwen QA: NOT RUN

- Development candidate: `candidate-k-16` (locked_validation_completed)
- Selection metric: `hybrid.document_metrics_at_10.ndcg_at_k`
- Effective configuration: `{"candidate_k": 16, "chunk_overlap": 120, "chunk_size": 1200, "context_budget_chars": 8000, "rrf_k": 60, "top_k": 5}`
- Paired development cases: 328; mean delta 0.0456; 95% bootstrap CI `[0.032780783405020025, 0.05914165670048385]` (2000 resamples).
- Locked holdout: COMPLETED once; run `20260928T090914Z-7586bc13ce`; SHA `241d2c7ebdc4911e92a18bdc1e48307d85c87503`.

- Sanitized server sync: NOT RUN.

## longbench-zh

- Dataset version: `LongBench-ZH-HF-5e628be450b7e67fb7ae6e201bd6d8f7056f7672-SEED20260928-V1`
- Indexed document pool: 400
- Cases: development 320; locked holdout 80
- Qrels semantics: `paired_task_context_diagnostic_not_official_qrels`
- Context relevance is a paired task-source diagnostic, not official retrieval qrels; span recall: `NOT_AVAILABLE`.
- Source terms: {"repository_code": "MIT", "underlying_data": "heterogeneous source rights; item-level terms unresolved"}

Failed attempts retained for diagnosis:

| Phase | Split | Profile | Stage | Error | Documents | Queries attempted | Git SHA |
|---|---|---|---|---|---:|---:|---|
| qa | development | smoke | index_validation | `source_coordinates_mismatch` | 50 | 0 | `42e4464f6caaebcb10952079a9cf859f4e943356` |

| Phase | Split | Profile | Documents | Queries | Variant | nDCG@10 | Recall@5 | Context recall@5 | Retrieval ms | Git SHA |
|---|---|---:|---:|---:|---|---:|---:|---:|---:|---|
| qa | development | smoke | 50 | 12 | baseline | 1.0000 | 1.0000 | 1.0000 | 83.92 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 400 | 320 | baseline-t5-c32-r60 | 0.8475 | 0.9219 | 0.9187 | 356.13 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 400 | 320 | candidate-k-16 | 0.8432 | 0.9219 | 0.9187 | 358.36 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 400 | 320 | candidate-k-64 | 0.8502 | 0.9281 | 0.9250 | 357.77 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 400 | 320 | rrf-30 | 0.8471 | 0.9219 | 0.9187 | 356.98 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 400 | 320 | top-k-10 | 0.8475 | 0.9219 | 0.9187 | 369.86 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 400 | 320 | top-k-3 | 0.8475 | 0.9219 | 0.8719 | 353.57 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| optimize | development | standard | 400 | 320 | top-k-8 | 0.8475 | 0.9219 | 0.9187 | 365.52 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| qa | development | smoke | 50 | 12 | baseline | 1.0000 | 1.0000 | 1.0000 | 67.11 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |
| validate | locked_holdout | standard | 400 | 80 | candidate-k-64 | 0.7979 | 0.8875 | 0.8750 | 378.08 | `241d2c7ebdc4911e92a18bdc1e48307d85c87503` |

Qwen QA: 8 cases; successful Qwen answer cases 8; normalized exact match 0.0000; character F1 0.1115; citation readback rate 1.0000; configured QA cases 8.

- Development candidate: `candidate-k-64` (locked_validation_completed)
- Selection metric: `hybrid.document_metrics_at_10.ndcg_at_k`
- Effective configuration: `{"candidate_k": 64, "chunk_overlap": 120, "chunk_size": 1200, "context_budget_chars": 8000, "rrf_k": 60, "top_k": 5}`
- Paired development cases: 320; mean delta 0.0026; 95% bootstrap CI `[-0.007366965987572281, 0.01414909156059477]` (2000 resamples).
- Locked holdout: COMPLETED once; run `20260928T093926Z-3cda58550a`; SHA `241d2c7ebdc4911e92a18bdc1e48307d85c87503`.

- Sanitized server sync: NOT RUN.

## Interpretation and limits

- The locked split is reportable only after a full standard validation run; the adapter records one-time use per dataset.
- Retrieval metrics are document-level. No span-level recall is inferred from document IDs.
- Qwen answer exact-match and character F1 are task diagnostics; judge and citation-support scoring remain `NOT_EVALUATED` unless a separately specified verifier actually ran.
- Source licensing and attribution notes are dataset-specific. LongBench underlying item rights remain heterogeneous and unresolved.
- Public server sync status is recorded separately after strict local bundle validation and deployed-code SHA comparison.

## Artifacts

Full source hashes, per-case outputs, model digests, index fingerprints, paired bootstrap comparisons, and run manifests are retained under the configured local data root. This report intentionally contains only aggregate/provenance fields.

## 2026-09-28 closeout audit addendum

Audit addendum updated at 2026-09-28T19:50:12+08:00 (Asia/Shanghai). This report was regenerated from completed run manifests, report JSON, per-query cases, and candidate records. The MIRACL-ZH 99-query and LongBench-ZH 80-query locked runs are both `COMPLETED` once. Case counts, unique qids, manifest sample counts, 12 source-file SHA-256 values, config/dataset/index/model/Git identities, and all three one-time lock manifest hashes match.

The user stopped the remaining grid after a total Goal time of 6:25:48. The SciFact 400/40 development unit ran 20:05 before interruption; its status is `INTERRUPTED`, not a test failure. It has 1,530 succeeded jobs, 3,652 queued, one expired lease, and 8,256 chunks/embeddings, but no complete manifest, metrics, cases, or stable index hash. `aggregation_eligible=false`; it is excluded from formal aggregates. Other unrun chunk, context, and dedup sweeps remain `NOT_RUN`.

LongBench QA has 8 scored answer cases (baseline and candidate-k-64 each answered the same 4 qids). Each QA run also made one unscored Qwen warmup call, so the two smoke runs made 10 Qwen answer API calls total: 8 scored cases plus 2 warmups. The 8 scored cases used the same 50-document smoke index (464 corpus embeddings), not the 400-document standard index (5,003 embeddings). The scored rows have EM 0 and combined character F1 0.1115; semantic Judge, Faithfulness, and Citation Support are `NOT_EVALUATED`. Citation readback is 7/7 among the seven rows with non-empty citations; one baseline row returned `UNSUPPORTED_ANSWER`.

Each development optimize run issued one BGE query embedding per parameter variant: 10,199 calls across 1,457 qids, or 8,742 calls above one embedding per qid. Standard development and locked runs with the same index hash also wrote separate corpus embeddings: 9,256 (SciFact), 6,001 (MIRACL), and 5,003 (LongBench) duplicated rows; the two 50-document QA smoke runs each wrote 464 embeddings with the same index hash. This identifies future in-run query caching and verified read-only index reuse opportunities; neither was changed or cleaned in this closeout.

No new model, evaluation, download, index build, or ECS sync was started. No benchmark-runner Python process was found; `ollama ps` was empty, and the Docker database container remained running. ECS was not synchronized because deployed SHA `f15aaeb…` differs from evaluation SHA `241d2c7…`.

Dataset-specific parameter tables, paired case failures, the 8 scored QA rows, cache/reuse conditions, and the bounded optional pilot are in [per-query and reuse analysis](public-benchmark-case-analysis-20260928.md). No production default or Release Tag changed.

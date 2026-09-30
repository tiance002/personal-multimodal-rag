# V1 retrieval decision — 2026-09-30

Status: **FROZEN FOR V1 PRODUCT VALIDATION**. This is a retrieval decision, not release acceptance or a release tag.

## Chosen default

**Adaptive: semantic Vector default + deterministic conditional Hybrid for exact phrases and identifiers.** Explicit Vector, Keyword and Hybrid modes remain available. Empty/unavailable vector recall uses one q0 keyword fallback without retrying embedding. Heavy rerank, MMR, rewrite and cloud fallback stay off.

Static Hybrid caused negative transfer under current corpus/model conditions, therefore retrieval routing was adopted.

## References and adaptations

WeKnora v0.8.2 supplies rank-based weighted RRF: vector 0.7, keyword 0.3, k=60. RAGFlow v0.27.2 supplies architectural comparison and optional ranker/fallback separation; its token/cosine similarity blend and thresholds are not copied. Our keyword leg is term frequency with phrase bonus, not BM25. See the pinned source table in `../reviews/rag-reference-adaptation.md`.

Project adaptations: candidate depth 32, final top 5, 8,000-character complete-chunk context, deterministic exact/identifier routing, no copied similarity threshold, local BGE-M3/Qwen and optional ranking ports. Every profile parameter has provenance in `backend/app/retrieval_profiles/v1_reference.json`.

## Bounded evidence and Gate

Execution SHA: `0b68385f237e9560ef6929297d459973a4def89d`.

Command: `& 'E:\RAG quention\.venv\Scripts\python.exe' -u -m scripts.validate_reference_retrieval --output var/reports/reference-sanity-20260930`. Exit 0, COMPLETED; 155.612 seconds including whole-index pre/postflight. Only 24 frozen Development QIDs (8 per dataset), 48 retrieval calls and 24 query embedding calls. No corpus embeddings, database writes, LLM calls, Locked or cloud calls. Model digests and all three original index fingerprints were unchanged; original container remained stopped.

| Dataset (n=8) | Vector Recall@5 | Static Reference Hybrid Recall@5 | Adaptive Recall@5 | Adaptive MRR@5 | Adaptive nDCG@10 | Adaptive Context Recall@5 |
|---|---:|---:|---:|---:|---:|---:|
| SciFact | .625 | .500 | .625 | .525 | .590023 | .500 |
| MIRACL zh | 1.000 | .625 | 1.000 | .854167 | .882118 | 1.000 |
| LongBench zh | .750 | .750 | .750 | .395833 | .522102 | .750 |

Adaptive MRR/nDCG/Context Recall equal Vector on this selection, with no additional lost-hit QIDs. Its reused arm p50 is 200.515 vs 178.170 ms for SciFact (+12.5%), identical 71.468 ms for MIRACL and 169.482 ms for LongBench. These small-sample cold/cache mixed observations show no severe regression; they are not a controlled production latency claim. Adaptive was selected offline from the two already executed arms, so its latency excludes router overhead. Only SciFact QID 933 used Hybrid; 23/24 used Vector.

**Gate PASS for this bounded sanity check**: Recall/MRR/nDCG remain at Vector level and no severe observed latency regression. This does not validate all identifier classes or prove best parameters. Fixed preregistration hash: `c9264d20df4fd48b8f645e0967552a96a9b8ff8e79c607aeea8c8f933ce00e72`. Local machine-readable results: `var/reports/reference-sanity-20260930/results.json`; public aggregate report is maintained separately.

## Alternatives and limitations

Always-on reference Hybrid lost 3/8 MIRACL hits relative to Vector and 1/8 SciFact hit. LongBench MRR and context recall also declined. Equal-weight legacy Hybrid is preserved as historical evidence; it is not re-executed. A parameter sweep or another adaptation run is unnecessary for this V1 decision.

Qrel-based retrieval is not answer quality. Character budgets are not Token counts. Context may drop a relevant source even when full fusion finds it. The 24-case public sanity sample is small; user-material validation now checks actual source grounding, exact lookups, identifiers, multi-document questions, follow-ups and failures. Any product limitation is reported separately without reopening public benchmark tuning.

## Future ranker plan

Ranker/diversity ports are optional and enforce authorized candidate identity. No concrete heavy model is installed. A later owner-approved study may evaluate one affordable ranker on the real-material frozen set, with actual quality, VRAM, Token and latency evidence. No automatic Judge, cloud policy, grid or index rebuild is part of this decision.

# Stable source Gold and metric units

The immutable Gold uses original source SHA, document identity and reviewed ranges in frozen normalized text. A real index is rebound through upload receipts, actual source bytes, actual parser output and stored chunk locators. The evaluator reproduces the production chunker at its actual settings; stale indexes paired with new declarations are INVALID.

## Ranking and source coverage are separate

`keyword`, `vector`, `fused` and `context` rank metrics are conventional **chunk** metrics. Qrels are derived from positive intersection with reviewed Gold over the entire scoped index, including relevant chunks not retrieved. Grade is the maximum intersected Gold grade. Non-intersecting text in the same document is irrelevant. No entire-document relevance assignment is used.

- Recall@K: distinct relevant returned chunk IDs / all relevant indexed chunk IDs.
- Precision@K: distinct relevant returned chunk IDs / fixed K; insufficient results do not shrink the denominator.
- F1, Hit, MRR, MAP and graded exponential-gain nDCG use the standard primitives in `metrics.py`. Repeated identical chunk IDs consume a rank without additional gain. MAP denominator is min(K, number of positive qrels).
- No-answer cases have null rank quality metrics, tracked separately by evaluated/unavailable counts and no-answer retrieval-empty indicator.

**Chunk Recall denominators can change with chunking. They must not be presented as stable evidence recall across A/B indexes.** Related overlap chunks are separate ranked results and separate model costs; their rank credit does not multiply source Gold credit.

`<stage>.source_recall_at_k` unions all retrieved intersections per stable Gold ID and compares coverage with the fixed80% threshold. `<stage>.evidence_coverage` averages covered fractions over Gold IDs. Multiple split chunks can jointly satisfy one Gold; overlapping intervals contribute once. A chunk can cover several Gold ranges, all counted once each. Distinct document/version/page/asset identities cannot intersect. Sheet coverage uses reviewed cell ordinals, not an entire sheet.

`context_recall` and `evidence_coverage` refer to the context selected by the production ContextBuilder, not the candidate sets. A retrieval-only prepared context is not proof of what was sent to a model; real generation acceptance must retain the actual Quick pipeline context and usage. Context budgets are characters and are never labelled as actual Tokens.

## Independent cloud consistency check

The source-statistics protocol transmits anonymous indexed chunk IDs, Gold IDs, relevance grades, Gold lengths, relative intervals and ranked IDs. The cloud rebuilds qrels and computes rank and source metrics. It does not trust supplied numeric metrics. Private source text stays local; anonymous sufficient statistics and their hashes cannot independently prove the truth of private annotations. File integrity, code identity and statistical consistency are separate checks.

The earlier abstract v2 qrels protocol remains covered by deterministic tests. Real source experiments use the source-statistics branch, with all index bindings collected locally. Formal acceptance requires committed code, matching ECS code SHA, real isolated indexes and complete A/B evidence.

## Usage and timing

Each anonymous model call retains role, stage, status, provider input/output counts and elapsed time. Unknown failed/retried consumption keeps stage and business totals null. Business chat totals exclude Embedding and Judge. Query calls that never ran remain unavailable, not zero consumption. Embedding input counts can be actual even when the provider supplies no output-token count.

Context/Evidence estimates use explicitly separate metric names. Optional local `tiktoken==0.14.0` with `cl100k_base` was import/execution checked; it is an approximation for Qwen context, not Qwen provider usage. If unavailable, preserve unavailable. Latency percentiles use type7 interpolation with explicit sample counts. No TTFT is invented.

## Actual retry and context provenance
Each retrieval attempt retains its keyword/vector/fused ranking and is recomputed separately. The context ranking may contain up to2K items after the production gateway merges a targeted retry. Rank metrics atK use its firstK; context_recall/evidence_coverage use the entire actual selected context. context_mode separates prepared offline context, confirmed sent context, failed-call unconfirmed context and not_run. Missing answer/query usage remains unavailable; model warmup/ingestion calls are recorded separately from case means. Exact duplicate savings rebuild the counterfactual through the production ContextBuilder and label cl100k_base counts as estimated; false merge rate is unavailable without reviewed merges.

# Public Benchmark Dataset Adapters — Execution Plan

1. Add failing contract tests for source/case separation, explicit qrels, split gates, stable chunk-to-document folding, and unknown chunk rejection.
2. Implement deterministic preparation and validation for SciFact, MIRACL-ZH candidate-pool v1, and LongBench Chinese under the external D: data root.
3. Add an isolated real runner that uses production ingestion and retrieval, records raw chunk and folded document rankings, and reuses existing metrics.
4. Add `scripts/run_public_benchmark.ps1` with preflight, fetch, prepare, baseline, optimize, qa, validate, sync, report phases plus help/dry-run handling.
5. Run only affected tests, then execute real development baselines and a bounded LongBench Qwen smoke; lock and evaluate each held-out set once.
6. Produce local source-rich reports. Sync only after validating that the existing ECS contract can represent the dataset without changing relevance semantics; otherwise report the exact contract limitation.

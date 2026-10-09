# RuleRouter development diagnostics v1

`cases-v1.json` contains 36 self-contained synthetic request/context cases and
SIMULATED cached responses. They are developer rule diagnostics, with semantic
quality **NOT_REVIEWED**; they are not Owner-approved quality gold, real provider
results, calibrated thresholds or a cost-saving experiment.

Run `python -B scripts/replay_p5_router.py --output <new-report.json>` offline.
CHEAP_ONLY, DYNAMIC, EXPENSIVE_ONLY and ALL_CHEAP_SAME_B use the same immutable
prompt/Context/parameters. Cache identity includes model, prompt template and
the source hashes of the actual validation/postprocessing files. Changing any
bound input invalidates old responses. There is no provider, key loader or DB
construction in this script. Bill costs are unavailable, not zero; scenario
latencies and synthetic tokens are explicitly separate from actual usage.

Frozen `quality_v1` and `trust_v1` corpora remain unchanged. Future human review
should label missing evidence, correct abstention, silent error and ineffective
upgrade independently; these fixtures do not assert general answer correctness.

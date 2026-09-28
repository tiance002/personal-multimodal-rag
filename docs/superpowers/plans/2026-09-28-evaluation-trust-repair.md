# Evaluation Trust Repair Implementation Plan

User supplied the full design constraints and autonomous Goal execution. Execute inline; no extra approval gate is required for the authorized repairs.

## Design
Keep the standard-library cloud registry and existing business RAG components. Add deterministic evaluation primitives under eval_center (no production dependency on cloud evaluator), and explicit optional local execution telemetry in the Ollama/retrieval adapters. Bundle schema v2 supplies only opaque source/evidence/result identifiers, numeric locators/rankings, validated configuration, counts, hashes, usage and computed claims. The cloud recomputes claims and rejects discrepancies. V1 remains readable as unverified historical diagnostics, never official comparison data. Hashes prove integrity, not authenticity of privately held source relevance; document this boundary rather than claim cryptographic proof of local execution.

## Tasks and evidence
1. `eval_center/gold.py`, `eval_center/metrics.py`, corresponding tests: immutable source/version locator validation, union coverage, dedup-aware rank formulas, explicit no-answer handling. Red tests → implementation → hand-calculated green tests. Old fixtures converted explicitly to stable source coordinates; original files retained.
2. `backend/app/application/retrieval.py`, `backend/app/adapters/models/ollama.py`: collect actual candidates/effective instance configuration and provider usage without changing existing return contracts; Context Builder settings and index rows measured by local runner. Regression existing business tests.
3. `eval_center/contracts_v2.py`, existing contracts/store/CLI/server/UI/package: sufficient statistics, independent formulas, strict privacy, manifest consistency, diagnostic v1 migration; tamper each requested field and verify rejection and immutable same-ID import.
4. `eval_center/quality.py`, local run module: answer-point coverage, frozen citation readback, refusal detection, duplicate footprint and estimated context savings; keep local judge optional and unavailable distinct from zero. Tests with conflicting years/money/source versions.
5. `evaluations/trust_v2/`: 30+ questions independently checked against frozen nonprivate source material, varied question types, source hashes and Gold coordinates; six old cases stay smoke. Two isolated DB/KB/index configurations receive same source/Gold; real Worker, BGE, Qwen, usage, latencies, raw reports.
6. Full eval tests plus relevant production regression; commit the evaluator and dataset; deploy exactly that commit to loopback ECS with DB backup; migrate existing records without deleting them. Record deploy SHA/readiness.
7. Real imports, comparison, restart/idempotence/tamper and 5+ case UI numeric tracing; update final audit and new repair acceptance report, preserving previous FAIL history. Stop once core trustworthy and only P2 remains; no parameter optimization campaign/tag.

## Risks to pin in tests
- Gold span split across chunks: union coverage, no double-counted overlap, immutable version mismatch fails.
- Empty Gold: retrieval rank scores excluded, explicit no-answer/refusal metric denominator.
- More than one evidence per chunk: separate source coverage from result ranking relevance, no metric >1.
- Missing provider counts / timed-out calls / unavailable judge: null with explicit availability, never invented zero.
- Forged declarations, summary/case/sample/manifest and legacy v1: rejected or diagnostic, never official data.

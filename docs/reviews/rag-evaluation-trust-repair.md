# RAG evaluation trust repair — working evidence

2026-09-28. Status: **IN PROGRESS; prior final audit FAIL remains in force.** This is the repair log, not a final acceptance declaration. No release tag.

## Changes
Existing attached `codex/eval-center` worktree reused, base HEAD `a0a62e8ee78be4a176b81e3f61cf5b84dd6d364b`; previous uncommitted code preserved.

- `eval_center/gold.py`: stable source SHA/version/locator coverage, union intervals and cells, explicit legacy fixture conversion (labelled frozen fixture text).
- `eval_center/metrics.py`: Recall/Precision/F1/Hit/MRR/MAP/nDCG primitives; repeated result IDs consume rank with zero extra credit; fixed K precision denominator, no-answer exclusion, unavailable values and evaluated counts, latency percentiles.
- `eval_center/verification.py`: independently recompute four-stage rank and context coverage claims and reject inconsistent declarations; now connected to strict v2 import and deterministic quality counts. No P0 completion claim: production stable-Gold ranking integration and real runner remain incomplete.
- `backend/app/application/retrieval.py`: actual runtime limit/RRF snapshot, candidate and full fused ranks, time and fallback metadata, preserving business algorithm.
- `backend/app/adapters/models/usage.py` + Ollama: opt-in local usage capture, query/answer/embedding calls including failed/retried attempts, business/judge isolation, unknown usage null; existing answer string contract retained.

## Commands / evidence
Executed in the attached eval-center worktree with `E:\RAG quention\.venv\Scripts\python.exe`:

| Command | Exit / result |
|---|---|
| `python -m pytest -q eval_center/tests/test_gold.py eval_center/tests/test_metrics.py -p no:cacheprovider --tb=short` | red1:16 failed (missing features); green0:16 passed |
| `python -m pytest -q backend/tests/test_ollama_usage.py -p no:cacheprovider --tb=short` | red1:3 failed; green0:3 passed; deterministic local HTTP stub, not actual Ollama |
| `python -m pytest -q backend/tests/test_retrieval_execution_record.py -p no:cacheprovider --tb=short` | red1:4 failed (missing runtime metadata); green included in next regression |
| `python -m pytest -q backend/tests/test_retrieval_execution_record.py backend/tests/test_hybrid_retrieval.py backend/tests/test_retrieval_contract.py backend/tests/test_embedding_profile_isolation.py backend/tests/test_langchain_quick_chain.py backend/tests/test_quick_evidence_flow.py backend/tests/test_egress_matrix.py backend/tests/test_ollama_usage.py -p no:cacheprovider --tb=short` | 0:22 passed,6 existing dependency warnings |
| `python -m pytest -q eval_center/tests/test_verification.py -p no:cacheprovider --tb=short` | red1:9 failed; green covered by full eval suite |
| `python -m pytest -q eval_center/tests -p no:cacheprovider --tb=short` | 0:66 passed; unit/deterministic only |

Read-only SSH checks returned0: ECS active, unchanged deployed release `ad2fc3ec…`, two original legacy experiments retained. No deployment/upload this checkpoint. Private key paths and credentials are excluded from this repository log.

## Remaining required work
Actual PostgreSQL index/source collector and real runner; stable Gold integration into rank-unit definitions without overlapping Gold credit; actual usage/latency/degradation protocol and runner integration; tokenizer/dedup/quality real acceptance;33-case isolated actual A/B; code commit, matching ECS deploy, server recomputation and all live tamper probes; Dashboard reconciliation and final audit update. These are **NOT RUN / incomplete**, not waived. Normal business KBs untouched. **未实现的命令不得报告通过。**

## v2 export, migration and locked dataset checkpoint

- `contracts_v2.py`, `store.py`, `server.py`, static Dashboard: v2 sufficient statistics validated and recomputed; SQLite schema2 additive sidecar; v1 rows preserved as unverified diagnostics, official comparisons reject them.
- `export_v2.py`, package script: explicit v2 projection, hashed case/item/Gold IDs; original claims must validate. Full local questions, answers, paths and arbitrary metadata stay local. Unknown statistics reject the bundle. No downgrade to old schema.
- `runtime.py`: component effective limits, actual production chunker reproduction check against indexed slices, source/embedding fingerprint; clean Git commit required for official code SHA; local-only Ollama inventory supplies model digests. These helpers have deterministic tests, not real index integration yet.
- `quality.py`: answer-point alternative substring coverage, cited label readback counts and unanswerable refusal. These are explicitly deterministic checks, not semantic factuality/faithfulness scores; Judge NOT_EVALUATED. Exact duplicate experiment analysis only within immutable document/version; different year/amount/version regressions pass. No tokenizer means unavailable; injected tokenizer counts labelled estimated. Semantic false merge rate remains unavailable, never silently zero.
- `build_reviewed_dataset.py`, `dataset.py`, `evaluations/trust_v1/`: freeze33 project-document questions and two genuine original documentation snapshots.30 answerable/3 no-answer, including cross-document, multiple evidence, scope, version and terminology. Source SHA and exact half-open quote offsets checked. The locked file has its own SHA; in-place rebuild refuses changed contents before writes. Explicit stable-gold-v1 extension retains the original four field names/types; transient expected_chunk_ids are empty and the separate source Gold loader is authoritative. The old fixture validation rules remain intact.
- Review status is Agent source inspection before A/B, **human review NOT RUN**. This is a project-document acceptance dataset, not evidence of general-domain quality. Sources originate in pre-existing docs, no fabricated project budgets/people/facts. No question, answer or source text uploaded to ECS this checkpoint.

### New executed commands

All commands below use the attached worktree cwd and the primary absolute Python executable. No real PostgreSQL/Ollama evaluation is represented by these unit results.

| Command | Exit / evidence |
|---|---|
| `python -m pytest -q eval_center/tests/test_v2_packager.py -p no:cacheprovider --tb=short` | red1:1 failed valid v2 export,5 rejection tests passed; implementation followed |
| `python -m pytest -q eval_center/tests/test_v2_packager.py eval_center/tests/test_packager.py -p no:cacheprovider --tb=short` | 0:9 passed |
| `python -m pytest -q eval_center/tests/test_runtime.py -p no:cacheprovider --tb=short` | red1:module missing; later green included in20-test command |
| `python -m pytest -q eval_center/tests/test_runtime.py eval_center/tests/test_v2_import.py eval_center/tests/test_v2_packager.py -p no:cacheprovider --tb=short` | 0:20 passed |
| `python -m pytest -q eval_center/tests/test_quality.py -p no:cacheprovider --tb=short` | red1:module missing; green0:4 passed |
| `python -m pytest -q eval_center/tests/test_v2_import.py -p no:cacheprovider --tb=short` | red1:new quality-statistics integration failed,11 prior cases passed; integrated in final combined green command |
| `python -m eval_center.build_reviewed_dataset` | 0:SOURCE_ANCHORS_VERIFIED,33 samples,2 sources; rerun idempotent. One intervening rerun exit1 detected manifest CRLF formatting; comparison now canonical JSON metadata with immutable source/locked bytes |
| `python -m pytest -q eval_center/tests/test_reviewed_dataset.py -p no:cacheprovider --tb=short` | 0:4 passed, including immutable rebuild before-write check |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest -q eval_center/tests backend/tests/test_retrieval_execution_record.py backend/tests/test_hybrid_retrieval.py backend/tests/test_retrieval_contract.py backend/tests/test_embedding_profile_isolation.py backend/tests/test_langchain_quick_chain.py backend/tests/test_quick_evidence_flow.py backend/tests/test_egress_matrix.py backend/tests/test_ollama_usage.py -p no:cacheprovider --tb=short` | **0:117 passed,6 existing dependency deprecation warnings**, includes95 eval cases +22 associated business tests |
| `git diff --check` | 0, LF/CRLF informational warnings only |

Base HEAD remains `a0a62e8ee78be4a176b81e3f61cf5b84dd6d364b`; changes uncommitted, ECS still previous release, no Tag. The prior audit FAIL remains in force and this Goal stays active.

## Source-statistics integration and real preflight

### Files / behavior

`source_metrics.py` derives conventional chunk qrels from the full scoped index's intersections with immutable Gold, then independently unions stable-source coverage per stage. No entire-document relevance and no duplicate source Gold credit. A preliminary unimplemented one-Gold-per-rank-slot test design was discarded before implementation because it would undercount a chunk containing multiple Gold spans. The final protocol uses conventional chunk metrics plus explicitly separate source-unit recall/coverage; metric names, units and denominators are documented in `docs/evaluation-metric-protocol.md` and Dashboard. Chunk Recall must not be treated as the stable source A/B quality criterion.

`isolated_index.py` rejects remote/non-prefix databases before connection, preserves databases, verifies actual original bytes/source SHA, parser normalized text/hash, stored chunk positions/content and re-created production chunking. Fingerprints include actual embedding vectors and immutable source slices; counts read real rows. `telemetry.py` re-sums anonymous actual query/answer/retry calls and separates Judge, estimated context/evidence and unavailable usage. Retriever stage timing is measured on the actual component; no fabricated zero for an unused stage.

Strict v2 import/export now accepts the source mapping protocol, telemetry and finite machine error codes. Compatible v2 comparison exposed a real `float(None)` defect, now fixed without imputing zero. UI shows actual config/models/index counts, evaluated/unavailable counts, per-case anonymous statistics and percent changes. Browser acceptance remains NOT RUN.

### Command evidence

| Executed command / operation | Exit / result |
|---|---|
| `python -m pytest -q eval_center/tests/test_source_metrics.py -p no:cacheprovider --tb=short` | initial module missing:1; implemented5passed0; added actual production400/40 vs700/70 stable-Gold case:6passed0 |
| `python -m pytest -q eval_center/tests/test_telemetry.py -p no:cacheprovider --tb=short` | initial missing module:1; green0:4passed |
| `python -m pytest -q backend/tests/test_retrieval_execution_record.py -p no:cacheprovider --tb=short` | red1:new stage timing case failed,4passed; green0:5passed |
| `python -m pytest -q eval_center/tests/test_v2_import.py -p no:cacheprovider --tb=short` | source-statistic case red1→green; null comparison red pytest failure then green0:14passed |
| `python -m pytest -q eval_center/tests/test_isolated_index.py -p no:cacheprovider --tb=short` | 0:5passed; normal/remote DB rejected |
| `docker image ls --format '{{.Repository}}:{{.Tag}}'` | 0:pgvector/pgvector:pg16 cached; no image download |
| `docker run --detach --name rag-eval-trust0928-db --label codex.task=eval-trust-0928 --publish 127.0.0.1:25436:5432 --env POSTGRES_DB=postgres --env POSTGRES_USER=rag_eval --env ('POSTGRES_PASSWORD='+$evalPassword) pgvector/pgvector:pg16` | 0:new dedicated container; generated password retained only outside repositories, never included in this report |
| `docker exec rag-eval-trust0928-db pg_isready -U rag_eval -d postgres` | 0:accepting connections |
| `python -c` isolated DB connection preflight calling `create_isolated_database` and asserting `SELECT current_database()` | 0:ISOLATED_DATABASE_CONNECTION_PASS |
| `python -c` guarded child Alembic invocation `[sys.executable,'-m','alembic','-c','alembic.ini','upgrade','head']` with isolated URL only in process environment | 0:0013_message_run_link; actual pgvector0.8.6 |
| Local Ollama `serve` with loopback, existing model directory, NO_CLOUD=1 | running session79087; inventory identifies qwen3.5:4b and bge-m3:latest, no new model download |
| `python -c` real local `OllamaGateway.embed/query_expand/answer` under `capture_usage` | 0:1024dimensions, query46input/12output, answer64input/31output, business153tokens; embedding23actual input/outputunavailable. Raw local report `var/reports/trust-model-preflight.json`; no Judge score |
| `python -c` genuine upload/storage/Worker `run_once` for the two frozen documents, then `read_index_snapshot`; mutate only in-memory worker setting700 for rejection probe and restore1200 | 0:2docs/33chunks/33embedding rows, false declaration rejected `indexed_chunking_mismatch`; `var/reports/trust-index-preflight.json` |
| `python -c`33-case actual HybridRetriever + ContextBuilder + stable SourceSpan mapper against that isolated pgvector index | 0:33completed; initial chunkRecall .6666666667, stable source/context Recall .6833333333 over30answerable,3no-answer excluded; `var/reports/trust-retrieval-preflight.json` |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest -q eval_center/tests backend/tests/test_retrieval_execution_record.py backend/tests/test_hybrid_retrieval.py backend/tests/test_retrieval_contract.py backend/tests/test_embedding_profile_isolation.py backend/tests/test_langchain_quick_chain.py backend/tests/test_quick_evidence_flow.py backend/tests/test_egress_matrix.py backend/tests/test_ollama_usage.py -p no:cacheprovider --tb=short` | 0:135passed,6 existing warnings (before final extra source-variant case) |
| `python -m pytest -q eval_center/tests -p no:cacheprovider --tb=short` | 0:113passed after final source test and Dashboard changes |
| `git diff --check`; tiktoken installed metadata and cl100k_base import/encode | 0; tiktoken0.14.0, optional eval extra pinned. Estimator approximate, not actual Qwen Token usage |

The three inline preflight operations use the checked-in business classes/helpers; their raw outputs and actual source/index/model details remain in the referenced ignored local JSON reports. Connection credentials were read from an external runtime file into process variables only; no URLs/passwords/key paths saved to repository artifacts. Preflight inventory and schema checks are actual services, not mocks. Unit fake HTTP responses remain separately labelled deterministic.

### Acceptance limits / next action

These are **dirty-worktree preflights**, not a formally version-bound A/B or a replacement for required acceptance. No formal experiment manifest, cloud upload, repaired deployment, commit or Tag was created. Context for the33-case retrieval preflight is prepared offline, not a claim about model-sent input. The only real generation here is the explicitly separate one-question provider preflight. Earlier six-case smoke is retained.

Next build the reusable official runner: actual KnowledgeGateway/Quick attempts and input contexts, explicit handling of retry-merged context beyond per-call top_k, full usage/stage/degradation/dedup/quality statistics; clean committed runtime SHA; fresh1200/120 vs700/70 isolated indexes; selected real Quick generation; matching ECS code deployment with SQLite backup and installer digest/restart fixes; independent import/tamper/restart/real browser reconciliation. Normal knowledge bases remain untouched. The prior FAIL remains valid; Goal active.

## 2026-09-28 committed A/B runner preparation
- Actual production ingestion, KnowledgeGateway/Quick Chain and isolated PG runner added as eval_center/runner.py. Context records all selected evidence including retry merges; prepared/sent/unconfirmed/not_run are distinct. Cloud recomputes initial and per-attempt ranks plus full context coverage, actual usage, exact duplicate analysis and estimated Token Savings. No semantic Judge scores fabricated.
- Full evaluation + affected retrieval/Quick/context/citation/usage/schema regression:154passed exit0,6 existing deprecation warnings. Commands recorded in repair report. PowerShell deploy/upload parser and runner --help exit0. New deployment packages v2 closure, requires clean committed Git SHA, backs up live SQLite with integrity_check and prior unit/config/release, binds imports and health to deployed SHA, restarts after symlink switch. Real deployment remains NOT RUN until committed.
- Next: commit this source, run fresh A/B on33 frozen questions and Qwen subset, deploy same code version, import anonymous bundles, independently test cloud tampering and browser values. Previous FAIL remains until real acceptance completed.

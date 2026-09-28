# Public Benchmark Dataset Adapters

## Goal

Run SciFact, the bounded MIRACL-ZH shard-0 pool, and LongBench Chinese through the existing production ingestion and retrieval components. Keep raw and prepared data under `D:\RAG-Public-Bench`; retain the existing `eval_center.metrics` formulas and ECS validation contract.

## Data contract

- Every prepared corpus row has only `doc_id`, `title`, and `text`. Questions, answers, qrels, split labels, and scoring metadata live in case/qrels files and never enter the indexed source text.
- Every case retains its original `qid`, split, question, source dataset version, and qrels at the dataset's declared relevance level. MIRACL grade-zero judgments stay explicit.
- A locked split can be loaded only by the final validation phase. Development cases are used for baselines and tuning.
- LongBench retrieval can retain the supplied context-to-question pairing as a diagnostic, but the report must label it as a derived task-source link, not official retrieval qrels. Exact source spans remain unavailable.

## Ranking contract

The production retriever returns chunk IDs. The adapter maps each indexed chunk to its original source document, then folds a ranked chunk list to unique document IDs by first occurrence. Chunk ranking and document ranking are recorded separately. Unknown chunk IDs invalidate the run. The existing `ranking_metrics` function computes document-level qrels metrics; it is not copied or reimplemented.

## Runtime and isolation

- Index with the real parser, chunker, BGE-M3 embedding provider, PostgreSQL/pgvector repository, keyword retrieval, vector retrieval, fusion, and context builder.
- Give every run arm a newly named guarded `rag_eval_trust_*` database and one KB. Never connect to or mutate the normal KB.
- Bind source document IDs from upload receipts, source hashes, effective config, model digests, index fingerprint, and committed Git SHA into the local run manifest.
- Store full raw evidence locally under the D: data root. ECS exports may contain allowlisted anonymous metrics and verified run provenance only.

## First implementation slice

Implement source preparation/loading, split and leakage validation, stable document mapping, and document-level ranking metrics first. Then add a reusable real-index runner and unified PowerShell entry point. Do not change default business settings, deploy the RAG product, or create release tags.

# ADR 004 RAG Evidence Quality and Qwen3.5 4B

**Date:** 2026-09-26

**Status:** implementation in progress

**Scope:** local model routing, query targets, evidence coverage, citations and chunk metadata

## Decision

The default local Chat model is `qwen3.5:4b`; `bge-m3:latest` remains the
independent 1024-dimensional Embedding model. A simple Quick request performs
deterministic normalization and hybrid retrieval before any optional query
model call. Local query expansion is disabled by default and is eligible only
after the first retrieval returns no candidates and the switch is enabled.

An unambiguous parallel or cost-difference question becomes explicit evidence
targets. Quick may perform one directed supplementary retrieval. Missing
targets cause a cited partial answer when some targets are supported, or an
`INSUFFICIENT_EVIDENCE` refusal when none are. Relationship questions require
explicit relationship evidence. Smart validates the same target coverage before
submitting an answer.

Only complete chunks selected for the answer context are frozen. Smart search
returns the same server-assigned `citation_label` that will be frozen, which
gave three consecutive passing real Qwen Smart citation smokes after two
intermittent missing-citation failures with the older chunk-ID-only result.
Model answers
must cite their factual claims. For explicit numeric targets, the subject,
field and number must agree with the cited source. Successful runs persist
only citations actually used. The deterministic check is conservative; complex
semantic entailment remains a measured future capability rather than an
unverified claim.

The chunker chooses heading, merged-section, page, paragraph or fixed fallback
from parsed structure. Immutable document versions record `parser_version`,
`chunker_version=adaptive/v1` and the actual `chunk_strategy` via migration
`0012_chunk_strategy`. Historical versions retain `legacy/fixed-v1` metadata.
The new quality data uses an explicit `quality-v1` evaluation schema, allowing
empty expected evidence only for refusal cases; the original `core-v1`
validator remains strict.

## Local evidence

```text
E:\ollama\ollama.exe show qwen3.5:4b
exit=0; architecture=qwen35; parameters=4.7B; capabilities=tools,thinking,completion,vision

\.venv\Scripts\python.exe scripts\model_probe.py --chat-model qwen3.5:4b --embedding-model bge-m3:latest --report var\reports\model-probe-qwen35-4b.json
exit=0; structured JSON valid; Qwen structured response 4153.1 ms; BGE vector dimension 1024

\.venv\Scripts\python.exe scripts\smoke_langchain_ollama.py --chat-model qwen3.5:4b --report var\reports\smoke-langchain-qwen35-4b.json
exit=0; one valid tool call, one tool result and one final answer

\.venv\Scripts\python.exe scripts\smoke_m1.py --real-model --database-url postgresql+psycopg://rag:rag@127.0.0.1:55432/rag --storage-root var\smoke-m1-qwen-storage --report var\reports\smoke-m1-qwen.json
exit=0; PostgreSQL/pgvector ingestion, Qwen Quick answer and citation readback PASS

\.venv\Scripts\python.exe scripts\smoke_m4.py --real-model --database-url postgresql+psycopg://rag:rag@127.0.0.1:55432/rag --report var\reports\smoke-m4-qwen.json
exit=0; Qwen Smart API, search+read, E1 citation readback PASS

\.venv\Scripts\python.exe scripts\evaluate_rag_quality.py --report var\reports\eval-rag-quality.json
exit=0; deterministic fixture: 9/9 scenarios PASS; no Chat calls

\.venv\Scripts\python.exe scripts\smoke_quality_postgres.py --database-url postgresql+psycopg://rag:rag@127.0.0.1:55432/rag --report var\reports\smoke-quality-postgres.json
exit=0; two real documents indexed; compound answer cites E1 and E2 with one Qwen call;
missing C refuses with zero Qwen calls; document-scoped A-only query returns
a partial answer with zero Qwen calls; both cited chunks re-read successfully
```

These are machine-local observations. The fixed fixture does not prove semantic
support accuracy or production recall across arbitrary documents.

## Local OCR evidence

PyMuPDF uses local Tesseract `eng+chi_sim` language packs pinned by SHA-256 in
`scripts/setup_ocr.ps1`; no document content is sent to a cloud OCR service.
Scanned pages and PDF embedded images are stored as immutable source assets,
and OCR text is a separately attributed derivative. OCR chunks retain the
source asset ID and PDF page number, with `chunk_assets` linking persisted IDs.
The M2 gate now checks the data files and runs a real PostgreSQL lineage test.
Blank scans are `OCR_EMPTY`; missing/unusable OCR is `OCR_UNAVAILABLE`.

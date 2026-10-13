# P2 document lifecycle port

## Fixed source behavior

This port follows the fixed WeKnora source at `E:\RAG-reference\WeKnora-3e8b0bfc`:

- `internal/application/service/knowledge_process.go:3406` — `(*knowledgeService).ProcessDocument` is the asynchronous document entry point; it resolves the KB and source before processing.
- `internal/application/service/knowledge_process.go:157-236` — `processDocumentFromPassage` persists chunks and proceeds through enabled indexing stages; parent and child chunks are handled separately.
- `internal/application/service/knowledge_process.go:341` — `processChunks` is the chunk persistence/indexing path.
- `internal/application/service/knowledge_post_process.go:83-100` — `KnowledgePostProcessService.Handle` prepares downstream document processing.
- `internal/application/service/knowledge_post_process.go:208-295` — summary is enabled by effective process configuration when text chunks exist; completion is immediate only when `expectedSubtasks == 0`.
- `internal/application/service/knowledge_post_process.go:328-353` — nonzero work is promoted into finalizing state and summary state is recorded before tasks are enqueued.

Accordingly, a text upload is not marked complete merely because parsing and chunk insertion succeeded. This port requires the injected post-processor to report completion. Index rows are kept disabled until all configured work succeeds, then visibility and document completion change together.

## Python contract and ordering

`backend/weknora_port/documents.py:122` defines `DocumentLifecycleService`. It accepts an identity-verified connection factory, an isolated local storage root, a constructor-bound `LocalPrincipal`, a KB settings resolver, and optional embedding, asset, and post-processing callbacks. There are no environment reads or provider defaults in this service.

The callable surface is:

- `create_knowledge_base(principal, name)`; `list_knowledge_bases(principal)`; `list_documents(principal, knowledge_base_id)`.
- `upload_document(principal, knowledge_base_id, filename, content_type, source)`; `read_document(...)` returns the original file bytes and safe filename/media type.
- `read_source_chunk(principal, chunk_id)` returns a tenant-checked source passage and document reference.

An upload receives new document and source-version UUIDs. The original is stored with create-exclusive semantics below `<storage_root>/<tenant>/<kb>/<document>/<source-version>/original/`; parsed assets, when present, are also written under a versioned immutable path. The DB row starts as `processing`. Parsing dispatch uses the existing `ingestion.ingest_document`/`ParserRegistry`, then `storage.chunks.prepare_chunks` creates the storage-neutral plan. Parsed chunks are inserted through `ScopedKnowledgeRepository.create_chunks`; configured indexing goes through `HybridIndexer`. Index records are initially disabled. Only after the injected post-processor succeeds does `_finalize` enable records and move the knowledge row from `processing` to `completed` in one SQL transaction. Parsing, callback, persistence, and cancellation failures retain a safe lifecycle status/code; no raw exception text is returned.

`LocalPrincipal` must exactly match the constructor-bound principal on every public operation. Repository writes additionally bind the trusted owner, tenant, KB, and document IDs. The post-processor and asset processor receive those resolved identities; returned image chunks are checked against the same owner/KB/document/source-version tuple before persistence.

## Explicit pending states

- If vector indexing is enabled and no embedding callback is configured, upload remains `pending` with `EMBEDDING_ADAPTER_REQUIRED`.
- If image processing is enabled but parsed assets need a missing asset adapter, upload remains `pending` with `MULTIMODAL_ADAPTER_REQUIRED`. Direct image upload is rejected when `enable_multimodal` is false.
- If text chunks exist but no post-processor is configured, upload remains `pending` with `POSTPROCESSOR_ADAPTER_REQUIRED`. This preserves the fixed source's default summary work instead of claiming completion early.
- A post-processor returning `False` leaves the row pending. Exceptions produce a failed safe code; cancellation records `cancelled` before propagating cancellation.

These injected callbacks are production integration seams, not substitutes for real adapters. Tests use synthetic callbacks only. No embedding, OCR, VLM, summary-model, external service, or actual PostgreSQL call ran in this validation.

## Verification evidence

Command (worktree root `C:\Users\22088\.codex\worktrees\weknora-python-port\RAG quention`):

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest -q --noconftest tests/weknora_port/test_documents.py
```

Exit code `0`; output `3 passed in 0.31s`. The tests exercise TXT and Markdown parser routing through the real ingestion dispatcher, immutable original files, chunk persistence and source readback, completed finalization with an injected synthetic post-processor, missing-postprocessor pending state, and principal/KB rejection before file creation. This is a fake-storage/unit result.

Command:

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest -q --noconftest tests/weknora_port/test_documents_pg.py
```

Exit code `0`; output `1 skipped`. The test is gated on the explicitly approved `WEKNORA_PORT_TEST_DSN`; it skipped because that variable was not supplied. Actual isolated-PostgreSQL lifecycle verification is therefore `NOT RUN`, not passed.

## Remaining integration

The runtime must inject actual configured embedding, multimodal/OCR/VLM, and summary/post-processing adapters before enabling those KB features. Database creation, schema application, and principal resolution remain owned by the root integration. This module does not start external workers or claim full P2 acceptance on its own.

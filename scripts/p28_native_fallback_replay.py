"""Offline replay of frozen P25/P27 retrieval candidates through native stages.

No search, embedding, generation, or network model request is made.  Retrieval
results and provider rerank responses are read from the frozen run artifacts;
production rerank/merge/context and runtime merge/projection adapters execute.
P25 identity/source rows are read from the pre-existing local PG fixture in an
explicit READ ONLY transaction.  The DB DSN is never emitted.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

P25 = Path("E:/WK/run/p25-20261017")
P27 = Path("E:/WK/run/p27-live-20261017")
OUT_DEFAULT = Path("E:/WK/run/p28-20261017")
P25_CASES = {
    "p25-row0434": "f8a2adbd0be91176dfacf5d46070387e8d6d3a20051e6672b28bb542f17063e2",
    "p25-row0959": "bd69db7fc3c825088a209705360c79ce70bd2296b10359939e6aac4ae3d3e532",
}
P27_CASES = {"p27-row0958": "c15932c0c402d353ec1490372ab163bf01c777dd999be9d5e074a6250c157eb9"}
PG_SYSTEM_ID = "7696088403504697382"
PG_DATABASE = "weknora_python_port_20261013"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def summarize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Keep full native values in the sidecar snapshot, hashes/excerpt in report."""
    text = str(row.get("content") or "")
    return {
        "id": row.get("id"), "knowledge_id": row.get("knowledge_id"),
        "knowledge_base_id": row.get("knowledge_base_id"), "chunk_type": row.get("chunk_type"),
        "chunk_index": row.get("chunk_index"), "start_at": row.get("start_at"), "end_at": row.get("end_at"),
        "source_version": (row.get("chunk_metadata") or {}).get("source_version"),
        "content_sha256": sha256_text(text), "content_chars": len(text),
        "parent_chunk_id": row.get("parent_chunk_id"), "pre_chunk_id": row.get("pre_chunk_id"),
        "next_chunk_id": row.get("next_chunk_id"),
    }


def trace_artifacts(case_id: str, trace_id: str) -> tuple[Path, Path]:
    if case_id.startswith("p25"):
        return (P25 / "runtime" / "stages" / f"{trace_id}.json",
                P25 / "runtime" / "rerank" / f"{trace_id}.json")
    return (P27 / "api-evidence" / "stages" / f"{trace_id}.json",
            P27 / "api-evidence" / "rerank" / f"{trace_id}.json")


def frozen_case(case_id: str, trace_id: str) -> dict[str, Any]:
    stage_path, rerank_path = trace_artifacts(case_id, trace_id)
    capture_path = (P25 / "runtime" / "captures" / f"{trace_id}.json") if case_id.startswith("p25") else None
    trace = read_json(stage_path)
    retrieval = next((s for s in trace["stages"] if s["stage"] == "retrieval"), None)
    if retrieval is None:
        raise ValueError(f"{case_id}: no persisted retrieval stage")
    rrfile = read_json(rerank_path)
    if len(rrfile.get("calls", [])) != 1:
        raise ValueError(f"{case_id}: rerank capture does not contain exactly one call")
    rr = rrfile["calls"][0]
    if rr.get("status") != "NORMAL_RESPONSE_RECEIVED":
        raise ValueError(f"{case_id}: no complete frozen rerank response")
    raw_results = rr.get("results") or []
    if any(not isinstance(r.get("index"), int) or not isinstance(r.get("score"), (int, float)) for r in raw_results):
        raise ValueError(f"{case_id}: malformed frozen rerank result")
    search_rows = retrieval["search_results"]
    if len(search_rows) != len(rr.get("documents", [])) and case_id != "p27-row0958":
        raise ValueError(f"{case_id}: frozen rerank request docs do not cover Search stage")
    return {
        "case_id": case_id, "trace_id": trace_id,
        "trace_path": str(stage_path), "rerank_path": str(rerank_path),
        "question": trace["question"], "query": rr["query"], "model_id": rr["model_id"],
        "search_rows": search_rows, "rerank_documents": rr.get("documents", []),
        "raw_rerank": raw_results, "trace_rerank": next((s.get("rerank_results", []) for s in trace["stages"] if s["stage"] == "rerank"), []),
        "trace_merge": next((s.get("merge_results", []) for s in trace["stages"] if s["stage"] == "merge"), []),
        "source_hashes": {"trace": sha256_bytes(stage_path.read_bytes()), "rerank": sha256_bytes(rerank_path.read_bytes())},
        "capture_path": str(capture_path) if capture_path else None,
        "capture_sha256": sha256_bytes(capture_path.read_bytes()) if capture_path else None,
        "saved_context": ((lambda x: {"result_ids": x["context"].get("result_ids", []),
                                         "rendered_contexts": x["context"].get("rendered_contexts", ""),
                                         "context_sha256": x.get("context_sha256")})(read_json(capture_path))
                         if capture_path else None),
    }


async def p25_snapshot(cases: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Read the actual versioned source chunks for frozen P25 Search IDs."""
    from scripts.weknora_port_mvp_real import connection

    all_ids: set[str] = set()
    trace_by_id: dict[str, dict[str, Any]] = {}
    for case in cases:
        for row in case["search_rows"]:
            all_ids.add(row["id"])
            trace_by_id[row["id"]] = row
            all_ids.update(i for i in (row.get("parent_chunk_id"), row.get("pre_chunk_id"), row.get("next_chunk_id")) if i)
    p25_state = read_json(P25 / "runtime" / "state.json")
    expected_tenant = int(p25_state["tenant"])
    conn = await connection()
    snapshots: dict[str, dict[str, Any]] = {}
    try:
        await conn.execute("BEGIN TRANSACTION READ ONLY")
        ident = await (await conn.execute("SELECT system_identifier, current_database() AS db FROM pg_control_system(), (SELECT current_database()) q")).fetchone()
        if str(ident["system_identifier"]) != PG_SYSTEM_ID or ident["db"] != PG_DATABASE:
            raise RuntimeError("P25_SOURCE_DATABASE_IDENTITY_MISMATCH")
        ids = sorted(all_ids)
        if not ids:
            raise RuntimeError("P25_EMPTY_SEARCH_CANDIDATE_SET")
        rows = await (await conn.execute(
            "SELECT c.id,c.tenant_id AS chunk_tenant_id,k.tenant_id AS document_tenant_id,"
            "c.knowledge_id,c.knowledge_base_id,c.content,c.source_content,c.chunk_index,"
            "c.start_at,c.end_at,c.chunk_type,c.parent_chunk_id,c.pre_chunk_id,c.next_chunk_id,c.image_info,"
            "c.metadata AS chunk_metadata,k.metadata AS document_metadata,k.parse_status,k.file_name,"
            "k.source AS knowledge_source,k.title AS knowledge_title,k.description AS knowledge_description,"
            "k.custom_metadata AS knowledge_custom_metadata "
            "FROM chunks c JOIN knowledges k ON k.id=c.knowledge_id AND k.knowledge_base_id=c.knowledge_base_id "
            "WHERE c.id = ANY(%s) AND c.deleted_at IS NULL AND k.deleted_at IS NULL", (ids,)
        )).fetchall()
        for row in rows:
            if row["chunk_tenant_id"] != expected_tenant or row["document_tenant_id"] != expected_tenant:
                raise RuntimeError("P25_CHUNK_DOCUMENT_TENANT_MISMATCH")
            source_text = row.get("source_content") or row.get("content") or ""
            chunk_meta = row.get("chunk_metadata") or {}
            doc_meta = row.get("document_metadata") or {}
            snapshot = {
                **row, "content": row.get("content") or "", "text": source_text,
                "source_version": chunk_meta.get("source_version"),
                "document_source_version": doc_meta.get("source_version"),
            }
            snapshots[row["id"]] = snapshot
            if row["id"] in trace_by_id and row["content"] != trace_by_id[row["id"]]["content"]:
                raise RuntimeError("P25_SAVED_SEARCH_CONTENT_DIFFERS_FROM_SOURCE_DB")
            if row["id"] in trace_by_id and (row["knowledge_id"], row["knowledge_base_id"]) != (
                    trace_by_id[row["id"]]["knowledge_id"], trace_by_id[row["id"]]["knowledge_base_id"]):
                raise RuntimeError("P25_SAVED_SEARCH_SCOPE_DIFFERS_FROM_SOURCE_DB")
            if snapshot["source_version"] and snapshot["document_source_version"] != snapshot["source_version"]:
                raise RuntimeError("P25_CHUNK_DOCUMENT_VERSION_MISMATCH")
    finally:
        try:
            await conn.execute("ROLLBACK")
        finally:
            await conn.close()
    if not set(trace_by_id) <= set(snapshots):
        raise RuntimeError("P25_SEARCH_CANDIDATE_SOURCE_CHUNK_MISSING")
    originals = [snapshots[row["id"]] for row in trace_by_id.values() if row.get("chunk_type") in ("text", "image_ocr", "image_caption")]
    version_ok = [bool(r.get("source_version") and r.get("source_version") == r.get("document_source_version")) for r in originals]
    if not all(version_ok):
        raise RuntimeError("P25_ORIGINAL_SOURCE_VERSION_VALIDATION_FAILED")
    identity = {"system_identifier": PG_SYSTEM_ID, "database": PG_DATABASE,
                "tenant_id": expected_tenant, "tenant_matches_chunk_and_document": True,
                "transaction": "READ ONLY", "candidate_ids": len(trace_by_id),
                "rows_snapshotted": len(snapshots),
                "candidate_content_exact_match_count": len(trace_by_id),
                "versioned_original_candidates": len(originals), "original_candidate_versions_match_document": all(version_ok),
                "summary_candidates_without_source_version": sum(row.get("chunk_type") == "summary" for row in trace_by_id.values())}
    return snapshots, identity


def p27_snapshot() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    p = P27 / "snapshots" / "final.json"
    data = read_json(p)
    chunks = {row["id"]: row for row in data["chunks"]}
    docs = {row["id"]: row for row in data["documents"]}
    case = frozen_case("p27-row0958", P27_CASES["p27-row0958"])
    source_api = read_json(P27 / "image-chunk-api-readbacks.json")
    source_api_by_id = {x["chunk_id"]: x["payload"]["data"] for x in source_api}
    candidate_ids = {row["id"] for row in case["search_rows"]}
    p27_state = read_json(P27 / "state.json")
    expected_tenant = int(p27_state["tenant"])
    relevant: dict[str, dict[str, Any]] = {}
    for chunk_id in candidate_ids:
        row = chunks.get(chunk_id)
        if row is None:
            raise RuntimeError("P27_SEARCH_CANDIDATE_MISSING_FROM_FINAL_SNAPSHOT")
        doc = docs[row["knowledge_id"]]
        if row.get("tenant_id") != expected_tenant or doc.get("tenant_id") != expected_tenant:
            raise RuntimeError("P27_CHUNK_DOCUMENT_TENANT_MISMATCH")
        frozen = next(x for x in case["search_rows"] if x["id"] == chunk_id)
        if (row["knowledge_id"], row["knowledge_base_id"]) != (frozen["knowledge_id"], frozen["knowledge_base_id"]):
            raise RuntimeError("P27_SAVED_SEARCH_SCOPE_DIFFERS_FROM_SOURCE_SNAPSHOT")
        meta = row.get("metadata") or {}
        version = meta.get("source_version")
        if version and doc.get("metadata", {}).get("source_version") != version:
            raise RuntimeError("P27_CHUNK_DOCUMENT_VERSION_MISMATCH")
        snap = {**row, "chunk_metadata": meta, "metadata": doc.get("metadata") or {},
                "knowledge_title": doc.get("title") or "", "knowledge_description": doc.get("description") or "",
                "knowledge_source": doc.get("source") or "", "knowledge_filename": doc.get("file_name") or "",
                "source_version": version,
                "document_source_version": doc.get("metadata", {}).get("source_version")}
        if chunk_id in source_api_by_id:
            api = source_api_by_id[chunk_id]
            if (api.get("chunk_id"), api.get("document_id"), api.get("knowledge_base_id"), api.get("source_version"), api.get("chunk_type")) != (
                    row["id"], row["knowledge_id"], row["knowledge_base_id"], version, row["chunk_type"]):
                raise RuntimeError("P27_API_SOURCE_READBACK_IDENTITY_MISMATCH")
            snap["text"] = api["text"]
            snap["readback_kind"] = "saved_api_source_chunk_readback"
        elif row["chunk_type"] == "text" and version:
            # final.json is the frozen PostgreSQL source snapshot, not a fresh API read.
            snap["text"] = row.get("source_content") or row.get("content") or ""
            snap["readback_kind"] = "saved_postgres_final_snapshot"
        else:
            snap["text"] = ""
            snap["readback_kind"] = "unavailable"
        relevant[chunk_id] = snap
    # Search rows do not carry linked parents in every case; merge may request them.
    for row in case["search_rows"]:
        for linked in (row.get("parent_chunk_id"), row.get("pre_chunk_id"), row.get("next_chunk_id")):
            if linked and linked in chunks and linked not in relevant:
                src = chunks[linked]
                doc = docs[src["knowledge_id"]]
                meta = src.get("metadata") or {}
                relevant[linked] = {**src, "chunk_metadata": meta, "source_version": meta.get("source_version"),
                                    "document_source_version": doc.get("metadata", {}).get("source_version"),
                                    "text": src.get("source_content") or src.get("content") or "",
                                    "readback_kind": "saved_postgres_final_snapshot"}
    original_candidates = [relevant[cid] for cid in candidate_ids if chunks[cid].get("chunk_type") in ("text", "image_ocr", "image_caption")]
    version_ok = [bool(r.get("source_version") and r.get("source_version") == r.get("document_source_version")) for r in original_candidates]
    if not all(version_ok):
        raise RuntimeError("P27_ORIGINAL_SOURCE_VERSION_VALIDATION_FAILED")
    return relevant, {
        "snapshot_path": str(p), "snapshot_sha256": sha256_bytes(p.read_bytes()),
        "snapshot_chunk_count": len(data["chunks"]), "candidate_ids": len(candidate_ids),
        "tenant_id": expected_tenant, "tenant_matches_chunk_and_document": True,
        "versioned_original_candidates": len(original_candidates), "original_candidate_versions_match_document": all(version_ok),
        "summary_candidates_without_source_version": sum(chunks[cid].get("chunk_type") == "summary" for cid in candidate_ids),
        "api_source_readbacks": sum(v.get("readback_kind") == "saved_api_source_chunk_readback" for v in relevant.values()),
        "postgres_snapshot_source_rows": sum(v.get("readback_kind") == "saved_postgres_final_snapshot" for v in relevant.values()),
        "api_readbacks_path": str(P27 / "image-chunk-api-readbacks.json"),
        "api_readbacks_sha256": sha256_bytes((P27 / "image-chunk-api-readbacks.json").read_bytes()),
    }


def to_search_result(row: dict[str, Any]):
    from backend.weknora_port.retrieval.types import SearchResult
    allowed = SearchResult.__dataclass_fields__.keys()
    return SearchResult(**{k: copy.deepcopy(v) for k, v in row.items() if k in allowed})


def snapshot_result(row: dict[str, Any]):
    """Build actual repository rows for merge/projection from source snapshots."""
    from backend.weknora_port.retrieval.types import SearchResult
    from backend.weknora_port.storage.knowledge import custom_metadata_text
    metadata = row.get("chunk_metadata") or {}
    # A DB snapshot has raw metadata, whereas saved P27 already uses this field.
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return SearchResult(
        id=row["id"], content=row.get("content") or "", score=0.0,
        knowledge_id=row["knowledge_id"], knowledge_base_id=row["knowledge_base_id"],
        chunk_type=row.get("chunk_type") or "text", chunk_index=int(row.get("chunk_index") or 0),
        start_at=int(row.get("start_at") or 0), end_at=int(row.get("end_at") or 0),
        parent_chunk_id=row.get("parent_chunk_id") or "", pre_chunk_id=row.get("pre_chunk_id") or "",
        next_chunk_id=row.get("next_chunk_id") or "", image_info=row.get("image_info") or "",
        metadata=row.get("knowledge_metadata") or row.get("document_metadata") or row.get("metadata") or {},
        chunk_metadata=metadata if isinstance(metadata, dict) else {},
        knowledge_source=row.get("knowledge_source") or "", knowledge_title=row.get("knowledge_title") or "",
        knowledge_filename=row.get("knowledge_filename") or row.get("file_name") or "",
        knowledge_description=row.get("knowledge_description") or "",
        knowledge_custom_metadata=(row.get("knowledge_custom_metadata") if isinstance(row.get("knowledge_custom_metadata"), str)
                                   else custom_metadata_text(row.get("knowledge_custom_metadata"))),
    )


class FrozenRepository:
    def __init__(self, rows: dict[str, dict[str, Any]]):
        self.rows = {key: snapshot_result(value) for key, value in rows.items()}

    async def get_chunks(self, ids, context):
        return {i: self.rows[i] for i in ids if i in self.rows}

    async def get_chunk_children(self, parent_ids, context):
        parents = set(parent_ids)
        return [r for r in self.rows.values() if r.parent_chunk_id in parents]

    async def get_faq_answers(self, ids, context):
        return {}


class FrozenDocumentReads:
    def __init__(self, rows: dict[str, dict[str, Any]]):
        self.rows = rows

    async def read_source_chunk(self, _principal, chunk_id):
        row = self.rows.get(chunk_id)
        if row is None or row.get("readback_kind") == "unavailable":
            raise LookupError("FROZEN_SOURCE_READ_UNAVAILABLE")
        return {"chunk_id": row["id"], "document_id": row["knowledge_id"],
                "knowledge_base_id": row["knowledge_base_id"], "source_version": row.get("source_version"),
                "chunk_type": row["chunk_type"], "start": row.get("start_at", row.get("start", 0)),
                "end": row.get("end_at", row.get("end", 0)), "text": row.get("text") or ""}


class ProjectionHarness:
    def __init__(self, reads):
        self.documents = reads
        self.principal = object()


class FrozenRerankModel:
    def __init__(self, case, expected_passages):
        from backend.weknora_port.models.types import RerankResult, RerankResponse
        self.case, self.expected_passages = case, expected_passages
        self.response = RerankResponse(tuple(RerankResult(int(x["index"]), float(x["score"]), str(x.get("text", "")))
                                             for x in case["raw_rerank"]))

    async def rerank(self, query, documents, context):
        if query != self.case["query"]:
            raise RuntimeError("FROZEN_RERANK_QUERY_MISMATCH")
        if list(documents) != self.expected_passages:
            raise RuntimeError("FROZEN_RERANK_DOCUMENTS_DO_NOT_MATCH_PRODUCTION_ENRICHMENT")
        return self.response


class FrozenRegistry:
    def __init__(self, case, passages):
        self.model = FrozenRerankModel(case, passages)

    async def rerank(self, model_id, context):
        if model_id != self.model.case["model_id"]:
            raise RuntimeError("FROZEN_RERANK_MODEL_ID_MISMATCH")
        return self.model


async def run_native(case: dict[str, Any], source_rows: dict[str, dict[str, Any]], *, rerank_enabled: bool) -> dict[str, Any]:
    from backend.weknora_port import runtime as runtime_module
    from backend.weknora_port.models.types import CallContext, TrustedScope
    from backend.weknora_port.retrieval import pipeline as pipeline_module
    from backend.weknora_port.retrieval.pipeline import PipelineState, RetrievalPipeline
    from backend.weknora_port.retrieval.rerank import enriched_passage
    from backend.weknora_port.retrieval.search import SearchTarget, SearchNothing

    search_results = [to_search_result(row) for row in case["search_rows"]]
    eligible = [(r, enriched_passage(r)) for r in search_results]
    eligible = [(r, text) for r, text in eligible if text.strip()]
    passages = [text for _, text in eligible]
    stored_docs = case["rerank_documents"]
    # For row0958 the saved provider request covers the four non-empty enriched
    # candidates, not the nine Search candidates. Require byte-for-byte equality.
    if stored_docs != passages:
        raise RuntimeError(f"{case['case_id']}: saved provider documents differ from production enriched_passage")

    kbs = tuple(dict.fromkeys(r.knowledge_base_id for r in search_results))
    docs = tuple(dict.fromkeys(r.knowledge_id for r in search_results))
    scope = TrustedScope("frozen-replay-owner", kbs, docs)
    context = CallContext(request_id=f"p28/{case['case_id']}/{('on' if rerank_enabled else 'off')}",
                          scope=scope, session_id="frozen-replay")
    targets = tuple(SearchTarget(kb, target_type="knowledge", knowledge_ids=docs) for kb in kbs)
    state = PipelineState(query=case["question"], rewrite_query=case["query"], intent="kb_search",
                          targets=targets, rerank_top_k=30, rerank_threshold=0.3,
                          rerank_model_id=case["model_id"] if rerank_enabled else "",
                          search_results=[], needs_retrieval=True)
    repo = FrozenRepository(source_rows)
    registry = FrozenRegistry(case, passages)

    async def fixed_understand(st, ctx, reg, **kwargs):
        st.rewrite_query, st.intent, st.needs_retrieval = case["query"], "kb_search", True

    async def fixed_search(st, ctx, repository, **kwargs):
        st.search_results = [copy.deepcopy(x) for x in search_results]

    old_understand, old_search = pipeline_module.understand_query, pipeline_module.search
    pipeline_module.understand_query, pipeline_module.search = fixed_understand, fixed_search
    try:
        adapter = runtime_module.ApplicationRuntime
        harness = ProjectionHarness(FrozenDocumentReads(source_rows))
        async def project(st, ctx):
            await adapter._project_source_evidence(harness, st, ctx, repo)
        pipeline = RetrievalPipeline(repo, registry, affinity_resolver=None,
            final_evidence_projection=project,
            merge_input_adapter=lambda st: adapter._source_merge_input(harness, st))
        try:
            final = await pipeline.run(state, context)
            status, error = "completed", None
        except SearchNothing as exc:
            final, status, error = None, "search_nothing", str(exc)
    finally:
        pipeline_module.understand_query, pipeline_module.search = old_understand, old_search

    output = {
        "case_id": case["case_id"], "rerank_enabled": rerank_enabled,
        "status": status, "error": error,
        "search_count": len(state.search_results),
        "search_ids": [r.id for r in state.search_results],
        "enriched_passage_count": len(passages),
        "rerank_request_document_sha256": [sha256_text(s) for s in passages],
        "rerank_raw_count": len(case["raw_rerank"]) if rerank_enabled else 0,
        "rerank_raw_scores": [{"index": x["index"], "score": x["score"]} for x in case["raw_rerank"]] if rerank_enabled else [],
        "rerank_raw_scores_by_candidate": [{"index": x["index"], "candidate_id": eligible[x["index"]][0].id,
                                             "score": x["score"], "request_text_sha256": sha256_text(eligible[x["index"]][1])}
                                            for x in case["raw_rerank"] if rerank_enabled and 0 <= x["index"] < len(eligible)],
        "accepted_rerank_count": len(state.rerank_results),
        "accepted_rerank": [{"id": r.id, "score": r.score} for r in state.rerank_results],
        "merge_count": len(state.merge_results),
        "merge": [{"id": r.id, "score": r.score, "chunk_type": r.chunk_type} for r in state.merge_results],
        "final_context_result_ids": list(final.result_ids) if final else [],
        "final_context_sha256": sha256_text(final.rendered_contexts) if final else None,
        "final_context_chars": len(final.rendered_contexts) if final else 0,
        "final_context_excerpt": final.rendered_contexts[:1400] if final else "",
    }
    return output


def posthoc_checks(case: dict[str, Any], on: dict[str, Any], off: dict[str, Any], source_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    accepted_trace_ids = [r["id"] for r in case["trace_rerank"]]
    trace_merge_ids = [r["id"] for r in case["trace_merge"]]
    raw_max = max((float(x["score"]) for x in case["raw_rerank"]), default=None)
    expected = {
        "saved_search_candidates_equal_replay": on["search_ids"] == [r["id"] for r in case["search_rows"]],
        "saved_candidate_content_exact_match": all(
            sha256_text(str(row.get("content") or "")) == sha256_text(str(source_rows.get(row["id"], {}).get("content") or ""))
            for row in case["search_rows"]),
        "original_source_versions_match_documents": all(
            bool(source_rows.get(row["id"], {}).get("source_version")) and
            source_rows[row["id"]].get("source_version") == source_rows[row["id"]].get("document_source_version")
            for row in case["search_rows"] if row.get("chunk_type") in ("text", "image_ocr", "image_caption")),
        "provider_request_exactly_reconstructed": on["enriched_passage_count"] == len(case["rerank_documents"]),
        "raw_score_max": raw_max,
        "saved_trace_rerank_ids": accepted_trace_ids,
        "saved_trace_merge_ids": trace_merge_ids,
        "replay_rerank_ids_match_saved_trace": [r["id"] for r in on["accepted_rerank"]] == accepted_trace_ids,
        "replay_merge_ids_match_saved_trace": [r["id"] for r in on["merge"]] == trace_merge_ids,
        "disabled_mode_uses_search_merge": off["accepted_rerank_count"] == 0 and off["merge_count"] > 0,
    }
    if case.get("saved_context") is not None:
        saved = case["saved_context"]
        if not saved["rendered_contexts"] and not saved["result_ids"]:
            expected["replay_context_ids_match_saved_capture"] = on["status"] == "search_nothing" and not on["final_context_result_ids"]
            expected["replay_context_rendering_matches_saved_capture"] = on["status"] == "search_nothing" and on["final_context_chars"] == 0
        else:
            expected["replay_context_ids_match_saved_capture"] = on["final_context_result_ids"] == saved["result_ids"]
            expected["replay_context_rendering_matches_saved_capture"] = on["final_context_sha256"] == sha256_text(saved["rendered_contexts"])
    if case["case_id"] == "p25-row0434":
        target = "085643dc-5ebb-4b8a-b7f1-75427da40015"
        expected["kaggle_chunk_in_search"] = target in on["search_ids"]
        expected["kaggle_chunk_survives_disabled_mode"] = target in [x["id"] for x in off["merge"]]
        expected["no_rerank_accepted"] = on["accepted_rerank_count"] == 0
    if case["case_id"] == "p25-row0959":
        expected["successful_comparison_has_two_accepted"] = len(accepted_trace_ids) == 2 and len(on["accepted_rerank"]) == 2
    if case["case_id"] == "p27-row0958":
        expected["nine_search_four_provider_docs"] = len(case["search_rows"]) == 9 and len(case["rerank_documents"]) == 4
        expected["no_rerank_accepted"] = on["accepted_rerank_count"] == 0
        expected["empty_rerank_stops_before_merge"] = on["status"] == "search_nothing" and on["merge_count"] == 0
        expected["disabled_mode_builds_native_merge_context"] = off["status"] == "completed" and off["merge_count"] > 0 and off["final_context_chars"] > 0
    return expected


async def async_main(out: Path) -> dict[str, Any]:
    cases = [frozen_case(name, tid) for name, tid in (*P25_CASES.items(), *P27_CASES.items())]
    p25, p27 = [c for c in cases if c["case_id"].startswith("p25")], [c for c in cases if c["case_id"].startswith("p27")]
    p25_rows, p25_identity = await p25_snapshot(p25)
    p27_rows, p27_identity = p27_snapshot()
    snapshots = {**p25_rows, **p27_rows}
    dump_json(out / "source-snapshot.json", {
        "p25_identity": p25_identity, "p27_identity": p27_identity,
        "chunks": {cid: {k: v for k, v in row.items() if k not in ("metadata", "document_metadata", "chunk_metadata")}
                   | {"metadata": row.get("chunk_metadata"), "content_sha256": sha256_text(str(row.get("content") or "")),
                      "source_text_sha256": sha256_text(str(row.get("text") or "")), "source_text_chars": len(str(row.get("text") or ""))}
                   for cid, row in snapshots.items()},
    })
    report_cases = []
    for case in cases:
        rows = {row["id"]: snapshots[row["id"]] for row in case["search_rows"] if row["id"] in snapshots}
        # Include only linked source candidates observed in frozen Search plus repository rows
        # required by merge parent/neighbor expansion.
        for row in case["search_rows"]:
            for linked in (row.get("parent_chunk_id"), row.get("pre_chunk_id"), row.get("next_chunk_id")):
                if linked and linked in snapshots:
                    rows[linked] = snapshots[linked]
        on = await run_native(case, rows, rerank_enabled=True)
        off = await run_native(case, rows, rerank_enabled=False)
        report_cases.append({
            "case_id": case["case_id"], "trace_id": case["trace_id"], "question": case["question"],
            "source_artifact_paths": {"trace": case["trace_path"], "rerank": case["rerank_path"]},
            "source_artifact_sha256": case["source_hashes"],
            "capture_artifact": ({"path": case["capture_path"], "sha256": case["capture_sha256"],
                                  "saved_context_chars": len(case["saved_context"]["rendered_contexts"]),
                                  "saved_context_sha256": sha256_text(case["saved_context"]["rendered_contexts"]),
                                  "saved_context_result_ids": case["saved_context"]["result_ids"]}
                                 if case.get("saved_context") is not None else None),
            "rerank_configuration_semantics": "rerank_enabled=False uses the existing Python runtime adapter that passes an empty rerank_model_id and causes native rerank to skip; it does not activate a fixed Go YAML enable_rerank flag.",
            "search_count": len(case["search_rows"]),
            "search": [summarize_row(row) for row in case["search_rows"]],
            "native_on": on, "native_off": off,
            "posthoc_checks": posthoc_checks(case, on, off, rows),
        })
    failed = [{"case_id": c["case_id"], "checks": [name for name, value in c["posthoc_checks"].items()
                                                       if isinstance(value, bool) and not value]}
              for c in report_cases]
    failed = [row for row in failed if row["checks"]]
    result = {
        "schema": "p28-native-frozen-replay-v1", "status": "completed" if not failed else "failed_validation",
        "failed_checks": failed,
        "execution": {"cloud_calls": 0, "embedding_calls": 0, "search_calls": 0,
                      "generation_calls": 0, "model_inference_calls": 0,
                      "rerank_toggle_semantics": "This replay compares the existing Python native empty-model-ID path; it is not evidence that fixed Go YAML enable_rerank is wired into Go's main chain.",
                      "scope_note": "The offline TrustedScope/owner are harness values for deterministic pipeline validation, not authorization evidence; real scope authorization remains covered by the independent P27 HTTP/PG run.",
                      "actual_pipeline_rerank": True, "actual_pipeline_merge": True,
                      "actual_runtime_merge_input_adapter": True, "actual_runtime_source_projection": True,
                      "source_projection_note": "P25 source rows came from verified read-only PG; P27 image rows from saved API readbacks and text rows from saved PostgreSQL snapshot."},
        "p25_pg_identity": p25_identity, "p27_snapshot_identity": p27_identity,
        "cases": report_cases,
    }
    dump_json(out / "replay.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=OUT_DEFAULT)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(async_main(args.out_dir), loop_factory=asyncio.SelectorEventLoop)
    print(json.dumps({"status": result["status"], "report": str(args.out_dir / "replay.json"),
                      "case_statuses": {c["case_id"]: {"on": c["native_on"]["status"], "off": c["native_off"]["status"],
                                                         "search": c["search_count"], "on_rerank": c["native_on"]["accepted_rerank_count"],
                                                         "off_merge": c["native_off"]["merge_count"], "off_context_chars": c["native_off"]["final_context_chars"]}
                                         for c in result["cases"]}}, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

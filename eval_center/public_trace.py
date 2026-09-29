"""Privacy-preserving trace construction for public evaluation runs."""
from __future__ import annotations

from typing import Any, Sequence

from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.retrieval import RetrievalItem
from backend.app.domain.models import ChunkRecord, RankedHit
from eval_center.gold import SourceSpan


def _chunk_rows(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["chunk_id"]): row for row in index.get("chunks", [])}


def _trace_row(hit: RankedHit, row: dict[str, Any], span: SourceSpan, decision: dict[str, object]) -> dict[str, object]:
    return {
        "chunk_id": hit.chunk_id,
        "rank": hit.rank,
        "raw_score": hit.raw_score,
        "fused_score": hit.fused_score,
        "retrieval_sources": list(hit.sources),
        "source_document_id": span.document_id,
        "source_version": span.source_version,
        "version_id": str(row.get("version_id")),
        "parent_id": span.document_id.split("#", 1)[0],
        "locator": span.locator(),
        "content_chars": len(row.get("content") or ""),
        "rendered_chars": decision["rendered_chars"],
        "used_chars_before": decision["used_chars_before"],
        "used_chars_after": decision["used_chars_after"],
        "selected": decision["selected"],
        "reason": decision["reason"],
    }


def build_stage_context_trace(
    qid: str,
    stage: str,
    rankings: Sequence[RankedHit],
    index: dict[str, Any],
    context_builder: ContextBuilder,
    *,
    top_k: int,
    effective_config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = _chunk_rows(index)
    spans: dict[str, SourceSpan] = index.get("spans", {})
    items: list[RetrievalItem] = []
    for hit in rankings:
        row = rows.get(hit.chunk_id)
        span = spans.get(hit.chunk_id)
        if row is None or span is None:
            raise ValueError(f"trace chunk is unavailable: {hit.chunk_id}")
        items.append(RetrievalItem(ChunkRecord(
            hit.chunk_id,
            str(row.get("knowledge_base_id", "")),
            span.document_id,
            str(row.get("version_id")),
            str(row.get("content", "")),
            locator=row.get("locator") or span.locator(),
            content_sha256=row.get("content_sha256"),
        ), hit))
    selected, decisions = context_builder.select_with_trace(items, top_k=top_k)
    candidates = [_trace_row(hit, rows[hit.chunk_id], spans[hit.chunk_id], decision)
                  for hit, decision in zip(rankings, decisions, strict=True)]
    citations = CitationService(InMemoryCitationStore())
    run_id = f"public-trace-{qid}-{stage}"
    context, labels = context_builder.build_selected(run_id, selected, citations)
    selected_ids = [item.chunk.chunk_id for item in selected]
    selected_sources: list[str] = []
    selected_parents: list[str] = []
    for chunk_id in selected_ids:
        source = spans[chunk_id].document_id
        parent = source.split("#", 1)[0]
        if source not in selected_sources:
            selected_sources.append(source)
        if parent not in selected_parents:
            selected_parents.append(parent)
    trace = {
        "status": "COMPLETE",
        "qid": qid,
        "stage": stage,
        "effective_config": dict(effective_config),
        "candidates": candidates,
        "selected_chunk_ids": selected_ids,
        "selected_source_ids": selected_sources,
        "selected_parent_ids": selected_parents,
        "used_chars": sum(int(row["used_chars_after"]) - int(row["used_chars_before"])
                           for row in candidates if row["selected"]),
        "rendered_chars": len(context),
        "citation_labels": labels,
    }
    return {"text": context, "selected_chunk_ids": selected_ids,
            "selected_document_ids": selected_sources, "citation_count": len(labels)}, trace


def read_stage_context_trace(variant: dict[str, Any], stage: str) -> dict[str, str]:
    trace = variant.get("context_trace", {}).get(stage)
    if not isinstance(trace, dict) or not isinstance(trace.get("candidates"), list):
        return {"status": "INSUFFICIENT_TRACE"}
    return trace

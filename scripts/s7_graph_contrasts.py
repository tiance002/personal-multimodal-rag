"""Freeze S7 Graph off/on generation inputs from real saved source responses.

No Provider construction, DB connection, network send or database write exists
here. Saved M4 Query/Search/BGE responses are reused, not scored as new calls.
The current Merge/TopK/Context and Graph selector run against one frozen whole
KB pool; scoring annotations never enter either arm's generation material.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT, Evidence
from backend.weknora_port.graph.quote_completion import parse_with_quote_completion
from backend.weknora_port.graph.retrieval import find_paths, fuse_paths
from backend.weknora_port.graph.types import GraphSource
from backend.weknora_port.models.types import CallContext, RerankResult, TrustedScope
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.filter_top_k import filter_top_k
from backend.weknora_port.retrieval.merge import merge
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.query import default_segmenter
from backend.weknora_port.retrieval.rerank import rerank
from backend.weknora_port.retrieval.search import SearchNothing, SearchTarget, validate_results
from backend.weknora_port.retrieval.types import SearchResult


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_identity(path):
    return {"path": str(path), "sha256": sha(Path(path).read_bytes())}


def current_rows(pool):
    documents = {row["id"]: row for row in pool["documents"]}
    result = {}
    for raw in pool["rows"]:
        row = SearchResult(**raw)
        document = documents[row.knowledge_id]
        if (row.knowledge_base_id != pool["knowledge_base_id"] or
                row.metadata.get("source_version") != document["source_version"] or
                document["parse_status"] != "completed" or document["enable_status"] != "enabled" or
                not row.is_enabled or row.chunk_type != "summary" and
                row.chunk_metadata.get("source_version") != document["source_version"]):
            raise ValueError("SNAPSHOT_SOURCE_VERSION_OR_ENABLED_MISMATCH")
        if row.id in result:
            raise ValueError("SNAPSHOT_DUPLICATE_CHUNK_ID")
        result[row.id] = row
    return result


class SnapshotRepository:
    def __init__(self, rows, context):
        self.rows, self.context = rows, context

    def _scope(self, row, context):
        return (context.scope == self.context.scope and
                row.knowledge_base_id in context.scope.knowledge_base_ids and
                (not context.scope.document_ids or row.knowledge_id in context.scope.document_ids))

    async def get_chunks(self, ids, context):
        return {key: deepcopy(self.rows[key]) for key in ids if key in self.rows and self._scope(self.rows[key], context)}

    async def get_graph_chunks(self, ids, context):
        return {key: row for key, row in (await self.get_chunks(ids, context)).items()
                if row.chunk_type in ("text", "image_ocr")}

    async def get_chunk_children(self, ids, context):
        return [deepcopy(row) for row in self.rows.values() if row.parent_chunk_id in ids and self._scope(row, context)]

    async def get_faq_answers(self, ids, context):
        if ids:
            raise ValueError("FROZEN_POOL_HAS_NO_FAQ_CONTRACT")
        return {}


def load_batches(build_dir, plan, rows):
    selected = set(plan["graph_build_plan"]["selected_documents"])
    records, seen, batches, identities = [], set(), [], []
    for path in sorted(Path(build_dir).glob("*.json")):
        saved = read(path)
        if "chunks" not in saved:
            continue
        if saved.get("tenant_id") != plan["tenant_id"] or saved.get("knowledge_base_id") != plan["knowledge_base_id"]:
            raise ValueError("GRAPH_BUILD_SCOPE_MISMATCH")
        if saved.get("document_id") not in selected:
            raise ValueError("UNPLANNED_GRAPH_DOCUMENT")
        identities.append(file_identity(path))
        for item in saved["chunks"]:
            source = GraphSource(**item["source"])
            if source.chunk_id in seen:
                raise ValueError("DUPLICATE_SOURCE_CONTRIBUTION")
            row = rows.get(source.chunk_id)
            if (row is None or source.tenant_id != plan["tenant_id"] or sha(source.owner_id) != plan["subject_sha256"] or
                    source.knowledge_base_id != plan["knowledge_base_id"] or source.document_id not in selected or
                    row.knowledge_id != source.document_id or row.metadata.get("source_version") != source.source_version or
                    row.content != source.content or row.start_at != source.start_at or row.end_at != source.end_at or
                    item["source_sha256"] != source.content_sha256):
                raise ValueError("GRAPH_BUILD_SOURCE_PROOF_MISMATCH")
            if not isinstance(item.get("raw_response"), str):
                raise ValueError("GRAPH_RAW_RESPONSE_MISSING")
            batch, mappings = parse_with_quote_completion(source, item["raw_response"], item["request_id"])
            seen.add(source.chunk_id)
            batches.append(batch)
            records.append({"source": asdict(source), "source_sha256": source.content_sha256,
                "raw_response_sha256": sha(item["raw_response"]), "nodes": len(batch.nodes), "edges": len(batch.edges),
                "rejected": list(batch.rejected), "quote_completions": list(mappings), "status": batch.status})
    expected = {row.id for row in rows.values() if row.knowledge_id in selected and row.chunk_type in ("text", "image_ocr")}
    if seen != expected:
        raise ValueError(f"GRAPH_BUILD_INCOMPLETE expected={len(expected)} present={len(seen)}")
    return tuple(batches), records, identities


class SavedRerank:
    def __init__(self, wire, response):
        self.wire, self.response, self.local_replays = wire, response, 0

    async def rerank(self, query, documents, context):
        if query != self.wire["query"] or list(documents) != self.wire["documents"]:
            raise ValueError("SAVED_RERANK_INPUT_NOT_IDENTICAL")
        self.local_replays += 1
        if self.local_replays != 1:
            raise ValueError("SAVED_RERANK_REPLAY_MUST_NOT_RETRY")
        return SimpleNamespace(results=tuple(RerankResult(index=value["index"], score=value["relevance_score"])
                                             for value in self.response["results"]))


class SavedRegistry:
    def __init__(self, model):
        self.model = model

    async def rerank(self, model_id, context):
        return self.model


def snapshot_stage(saved, name):
    return next(stage["state"] for stage in saved["stages"] if stage["stage"] == name and stage["status"] == "completed")


async def render_arm(initial, context, repo, paths, now, enabled):
    state = deepcopy(initial)
    state.graph_trace = []
    added = await fuse_paths(state, context, repo, paths) if enabled and paths else 0
    final = await into_chat_message(state, context, now=now)
    return {"finalized_context": asdict(final), "search_results": [asdict(row) for row in state.merge_results],
        "evidence": [asdict(Evidence.from_result(row)) for row in state.merge_results],
        "history": [], "language": "Chinese (Simplified)", "rendered_at": now.isoformat(),
        "system_prompt": DEFAULT_SYSTEM_PROMPT, "is_fallback": False,
        "graph_enabled": enabled, "added_chunks": added, "graph_trace": state.graph_trace,
        "context_chars": len(final.rendered_contexts), "context_sha256": sha(final.rendered_contexts),
        "user_content_sha256": sha(final.user_content),
        "sources": [{"chunk_id": row.id, "document_id": row.knowledge_id,
            "source_version": row.metadata.get("source_version"), "type": row.chunk_type,
            "sub_chunk_ids": row.sub_chunk_ids} for row in state.merge_results]}


async def fixed_case(case, historical, rows, batches, plan):
    capture = read(historical["stage_capture_path"])
    frozen = snapshot_stage(capture, "retrieval")
    if frozen["query"] != case["request"]["content"] or historical["question"] != frozen["query"]:
        raise ValueError("FROZEN_ORIGINAL_QUESTION_MISMATCH")
    calls = [call for call in historical["provider_calls"] if call["capability"] == "rerank"]
    if len(calls) != 1 or len(calls[0]["wire_capture_matches"]) != 1:
        raise ValueError("EXACTLY_ONE_SAVED_BGE_RESPONSE_REQUIRED")
    path = Path(calls[0]["wire_capture_matches"][0]["file"])
    request = read(path)
    response_path = path.with_name(path.name.replace(".request.json", ".response.body"))
    response = read(response_path)
    scope = request["trusted_scope"]
    scope = ast.literal_eval(scope) if isinstance(scope, str) else scope
    if (sha(scope["owner_id"]) != plan["subject_sha256"] or scope["knowledge_base_ids"] != [plan["knowledge_base_id"]] or
            scope["document_ids"] != case["request"]["document_scope"] or request["model"] != "BAAI/bge-reranker-v2-m3" or
            sha(json.dumps(request["body"], ensure_ascii=False, separators=(",", ":"))) != request["body_sha256"]):
        raise ValueError("SAVED_RERANK_SCOPE_OR_BODY_IDENTITY_MISMATCH")
    context = CallContext("s7-graph-offline-no-send/" + case["id"], TrustedScope(scope["owner_id"],
        tuple(scope["knowledge_base_ids"]), tuple(scope["document_ids"])))
    candidates = [SearchResult(**row) for row in frozen["search_results"]]
    for row in candidates:
        current = rows.get(row.id)
        if (current is None or (row.content, row.knowledge_id, row.knowledge_base_id, row.metadata.get("source_version")) !=
                (current.content, current.knowledge_id, current.knowledge_base_id, current.metadata.get("source_version"))):
            raise ValueError("HISTORICAL_CANDIDATE_NOT_CURRENT_IN_FROZEN_POOL")
    state = PipelineState(query=frozen["query"], rewrite_query=frozen["rewrite_query"],
        targets=(SearchTarget(plan["knowledge_base_id"]),), search_results=deepcopy(candidates),
        rerank_model_id=request["model_id"], rerank_threshold=.3, rerank_top_k=30,
        enable_rewrite=False, enable_expansion=False, history=[], source_evidence_mode="off")
    validate_results(candidates, state.targets, context)
    model = SavedRerank(request["body"], response)
    empty = False
    try:
        await rerank(state, context, SavedRegistry(model), default_segmenter())
    except SearchNothing:
        empty = True
    if model.local_replays != 1:
        # The production fallback can absorb a Provider-shaped exception; this
        # diagnostic must never mistake a rejected frozen wire for a replay.
        raise ValueError("SAVED_RERANK_WIRE_WAS_NOT_REPLAYED")
    historical_rerank = snapshot_stage(capture, "rerank")["rerank_results"]
    if [(row.id, row.score) for row in state.rerank_results] != [(row["id"], row["score"]) for row in historical_rerank]:
        raise ValueError("SAVED_BGE_FILTER_OR_MMR_BASELINE_MISMATCH")
    repo = SnapshotRepository(rows, context)
    if not empty:
        await merge(state, context, repo, default_segmenter())
        filter_top_k(state)
    state.merge_results = [] if empty else state.merge_results
    found = find_paths(state.query + "\n" + state.rewrite_query, batches, context, tenant_id=plan["tenant_id"])
    final_time = next(stage["time_utc"] for stage in capture["stages"]
                      if stage["stage"] == "context" and stage["status"] == "completed")
    now = datetime.fromisoformat(final_time)
    arms = {name: await render_arm(state, context, repo, found, now, enabled)
            for name, enabled in (("off", False), ("on", True))}
    return {"id": case["id"], "question": state.query, "query": state.query, "rewrite": state.rewrite_query,
        "classification": "REUSED_REAL_EXTRACTION_AND_M4_RETRIEVAL_NEW_LOCAL_GRAPH_CONTRAST",
        "scope": {"tenant_id": plan["tenant_id"], **scope}, "scoring_only": case["scoring_only"],
        "historical_preparation": {"session_id": historical["session_id"], "history": [],
            "history_rule": "Saved question was the first and only turn of its independent M4 session; current response is excluded.",
            "stages": file_identity(Path(historical["stage_capture_path"])), "rerank_request": file_identity(path),
            "rerank_response": file_identity(response_path), "wire_body_sha256": request["body_sha256"],
            "new_provider_calls": 0},
        "stages": {"search": [asdict(row) for row in candidates], "raw_rerank": response["results"],
            "rerank_mmr": [asdict(row) for row in state.rerank_results],
            "ordinary_merge_top_k": [asdict(row) for row in state.merge_results],
            "graph_paths": [asdict(path) for path in found]},
        "arms": arms,
        "limits": {"rerank_threshold": .3, "rerank_top_k": 30, "graph_paths": 3, "graph_hops": 2}}


async def execute(workdir, m4_root):
    plan_file, pool_file = workdir / "graph-plan.json", workdir / "graph-source-pool.json"
    plan, pool = read(plan_file), read(pool_file)
    expected_sha = next(value for key, value in plan["input_sha256"].items() if key.replace("\\", "/").endswith("/graph-source-pool.json"))
    if sha(pool_file.read_bytes()) != expected_sha or pool["tenant"] != plan["tenant_id"] or pool["tenant"] != 332:
        raise ValueError("FROZEN_POOL_IDENTITY_CHANGED")
    rows = current_rows(pool)
    batches, records, graph_files = load_batches(workdir / "graph-build", plan, rows)
    history_path = m4_root / "api-execution/qa_results.jsonl"
    historical = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
    cases = []
    for case in plan["cases"]:
        if case.get("planned_validation") != "FIXED_SAVED_M4_PREPARATION_GRAPH_OFF_ON_GENERATION":
            continue
        matches = [row for row in historical if row.get("qid") == case["qid"]]
        if len(matches) != 1:
            raise ValueError("EXACTLY_ONE_HISTORICAL_PREPARATION_REQUIRED")
        cases.append(await fixed_case(case, matches[0], rows, batches, plan))
    return {"schema": "s7-graph-contrasts/v1", "status": "PREPARED_NOT_NEW_GENERATION",
        "new_provider_calls": 0, "new_tokens": 0, "database_access": False,
        "files": [file_identity(plan_file), file_identity(pool_file), file_identity(history_path), *graph_files],
        "pool_documents": len(pool["documents"]), "pool_rows": len(rows),
        "graph_batches": records, "graph_nodes": sum(len(batch.nodes) for batch in batches),
        "graph_edges": sum(len(batch.edges) for batch in batches),
        "generation_parameters": {"thinking": False, "max_completion_tokens": 1024, "temperature": .3},
        "cases": cases,
        "not_run": {"franklin-mixed": "Await new normal API actual preparation", "franklin-maintenance-unanswerable": "Await new normal API actual preparation"},
        "limitations": ["No new query understanding/search/rerank/generation call", "Current pool is a read-only saved resource snapshot, not a live PG assertion",
                        "Targeted graph extraction subset; full 24-document pool remains available to normal Merge source reads"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, default=Path("E:/WK/run/s7-20261019"))
    parser.add_argument("--m4-root", type=Path, default=Path("E:/WK/run/m4-full-sweep-20261018"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(execute(args.workdir, args.m4_root))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "cases": len(result["cases"]),
                      "nodes": result["graph_nodes"], "edges": result["graph_edges"], "new_provider_calls": 0}))


if __name__ == "__main__":
    main()

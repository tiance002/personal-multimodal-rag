"""S7 bounded offline comparison. Never opens a database or Provider client.

The S6 arm reuses recorded model responses and retrieval snapshots under their
original identity. Other arms are explicitly synthetic engineering fixtures.
No output of this program is a new model send or an answer-quality acceptance.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.weknora_port.graph import extraction, retrieval
from backend.weknora_port.graph.quote_completion import parse_with_quote_completion
from backend.weknora_port.graph.types import GraphSource
from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget
from backend.weknora_port.retrieval.types import SearchResult


BASELINE = "855e2d32bf85f50fbb9fa12ace9b0a51e1adbf67"


def sha(value):
    data = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def identity(path):
    return {"path": str(path), "sha256": sha(path.read_bytes())}


def baseline_module(name):
    filename = f"backend/weknora_port/graph/{name}.py"
    data = subprocess.run(["git", "show", f"{BASELINE}:{filename}"],
                          cwd=ROOT, check=True, capture_output=True).stdout
    module = types.ModuleType(f"backend.weknora_port.graph._s7_baseline_{name}")
    module.__package__ = "backend.weknora_port.graph"
    sys.modules[module.__name__] = module
    exec(compile(data, f"{BASELINE}:{filename}", "exec"), module.__dict__)
    return module, {"commit": BASELINE, "path": filename, "sha256": sha(data)}


class SnapshotRepository:
    """Read-only in-memory original source snapshots; no fake DB acceptance."""

    def __init__(self, rows):
        self.rows = rows

    async def get_graph_chunks(self, ids, context):
        return {key: deepcopy(self.rows[key]) for key in ids if key in self.rows}


async def replay_arm(module, batches, context, ordinary, rows, query, rewrite,
                     *, enabled, now):
    s = PipelineState(query=query, rewrite_query=rewrite,
        targets=tuple(SearchTarget(key) for key in context.scope.knowledge_base_ids),
        merge_results=deepcopy(ordinary), enable_rewrite=False, enable_expansion=False)
    found = module.find_paths(query + "\n" + rewrite, batches, context,
        tenant_id=batches[0].source.tenant_id) if enabled else ()
    added = await module.fuse_paths(s, context, SnapshotRepository(rows), found) if found else 0
    final = await into_chat_message(s, context, now=now)
    return {"enabled": enabled, "path_count": len(found), "paths": [asdict(path) for path in found],
            "added_chunks": added, "trace": s.graph_trace,
            "result_ids": list(final.result_ids), "context_chars": len(final.rendered_contexts),
            "user_content_sha256": sha(final.user_content), "user_content": final.user_content,
            "raw_sources": {batch.source.chunk_id: {
                "identity_retained": any(row.id == batch.source.chunk_id or
                    row.chunk_type in ("text", "image_ocr", "parent_text") and
                    batch.source.chunk_id in row.sub_chunk_ids and
                    row.knowledge_id == batch.source.document_id and
                    row.metadata.get("source_version") == batch.source.source_version
                    for row in s.merge_results),
                "complete_body_in_context": batch.source.content in final.user_content}
                for batch in batches}}


async def reused_real(s6_root, baseline):
    graph_files = [s6_root / "acceptance-deepseek" / f"graph-{i}.json" for i in range(2)]
    replay_file = s6_root / "acceptance-deepseek/source-quote-replay.json"
    stage_files = list((s6_root / "acceptance-api-fixed/evidence").glob("*.stages.json"))
    if len(stage_files) != 1:
        raise ValueError("Exactly one saved S6 stage receipt is required")
    replay = read(replay_file)
    stages = read(stage_files[0])
    records = {item["document_id"]: item for item in replay["records"]}
    batches, lineage = [], []
    for graph_file in graph_files:
        saved = read(graph_file)
        for item in saved["chunks"]:
            source = GraphSource(**item["source"])
            record = records[source.document_id]
            if source.tenant_id != 1 or source.content_sha256 != item["source_sha256"] or source.content_sha256 != record["source_sha256"]:
                raise ValueError("S6 original identity/source SHA mismatch; no tenant substitution allowed")
            if sha(item["raw_response"]) != record["lineage"]["raw_response_sha256"]:
                raise ValueError("Original model response SHA mismatch")
            batch, mappings = parse_with_quote_completion(source, item["raw_response"], item["request_id"])
            if (len(batch.nodes), len(batch.edges), list(mappings)) != (record["nodes"], record["edges"], record["lineage"]["quote_completions"]):
                raise ValueError("S6 proof replay differs from saved accepted contribution")
            batches.append(batch)
            lineage.append({"source": asdict(source), "source_sha256": source.content_sha256,
                            "raw_response_sha256": sha(item["raw_response"]),
                            "quote_completions": list(mappings), "original_lineage": record["lineage"]})
    sources = [batch.source for batch in batches]
    if len({(s.tenant_id, s.owner_id, s.knowledge_base_id) for s in sources}) != 1:
        raise ValueError("Saved S6 scope identities disagree")
    context = CallContext("s7-offline-reused-s6-no-send", TrustedScope(sources[0].owner_id,
                         (sources[0].knowledge_base_id,)))
    snapshot = next(stage["state"] for stage in stages["stages"]
                    if stage["stage"] == "merge" and stage["status"] == "completed")
    final_state = next(stage["state"] for stage in stages["stages"]
                       if stage["stage"] == "graph_fusion" and stage["status"] == "completed")
    # The started event has no state payload. Fusion only appends match_type
    # graph rows; retain the recorded ordinary rows from its completed snapshot
    # to recover the actual post-TopK input, rather than guess TopK settings.
    ordinary = [SearchResult(**row) for row in final_state["merge_results"] if row["match_type"] != "graph"]
    before = {row["id"]: row for row in snapshot["merge_results"]}
    if any(asdict(row) != before.get(row.id) for row in ordinary):
        raise ValueError("Cannot recover unchanged ordinary post-TopK snapshot")
    rows = {row["id"]: SearchResult(**row) for row in
            snapshot["search_results"] + final_state["merge_results"] if row["id"] in {s.chunk_id for s in sources}}
    for source in sources:
        row = rows[source.chunk_id]
        if row.content != source.content or row.metadata.get("source_version") != source.source_version:
            raise ValueError("Saved raw chunk/version differs from graph source")
    # Freeze the metadata date that was actually rendered in the historical turn.
    original_content = stages["finalized_context"]["user_content"]
    date = original_content.split("Current time: ", 1)[1].split()[0]
    now = datetime.strptime(date, "%Y-%m-%d")
    arms = {}
    for name, module, enabled in (("graph_off", retrieval, False),
                                  ("baseline_graph_on", baseline, True),
                                  ("current_graph_on", retrieval, True)):
        arms[name] = await replay_arm(module, tuple(batches), context, ordinary, rows,
            snapshot["query"], snapshot["rewrite_query"], enabled=enabled, now=now)
    return {"classification": "REUSED_REAL_RESPONSE_NOT_NEW_SEND",
        "identity": {"tenant_id": 1, "owner_id": sources[0].owner_id,
                     "knowledge_base_id": sources[0].knowledge_base_id},
        "files": [identity(path) for path in [*graph_files, replay_file, stage_files[0]]],
        "nodes": sum(len(b.nodes) for b in batches), "edges": sum(len(b.edges) for b in batches),
        "lineage": lineage, "query": snapshot["query"], "rewrite": snapshot["rewrite_query"],
        "frozen_search": snapshot["search_results"], "frozen_rerank": snapshot["rerank_results"],
        "frozen_ordinary_after_top_k": [asdict(row) for row in ordinary],
        "ordinary_snapshot_proof": "Saved fusion-completed rows excluding appended graph rows, checked unchanged against merge-completed snapshot",
        "arms": arms,
        "baseline_matches_historical_context": arms["baseline_graph_on"]["user_content"] == original_content,
        "current_matches_historical_context": arms["current_graph_on"]["user_content"] == original_content,
        "new_provider_calls": 0, "new_tokens": 0,
        "limitation": "In-memory source replay cannot establish current PG validity or new model answer quality."}


async def synthetic(baseline, baseline_extract):
    from tests.weknora_port.test_graph_retrieval import CTX, chain, source_row
    from tests.weknora_port.test_graph_s7_retrieval import branches
    multi = branches(18)
    cases = []
    relation_questions = ("founded", "visited", "funded", "joined")
    cut_relation = next(edge.predicate for edge in sorted(multi[0].edges, key=lambda e: e.id)[12:]
                        if edge.predicate not in relation_questions)
    for predicate in (*relation_questions, cut_relation):
        query = f"Where is the institute Astrid Vale {predicate}?"
        result = {"query": query, "requested_relation": predicate}
        for name, module in (("baseline", baseline), ("current", retrieval)):
            paths = module.find_paths(query, multi, CTX, tenant_id=1, max_paths=1)
            result[name] = {"first_relation": paths[0].edges[0].predicate if paths else None,
                            "edges": [e.id for e in paths[0].edges] if paths else [],
                            "required_relation_selected": bool(paths and paths[0].edges[0].predicate == predicate)}
        cases.append(result)
    source = GraphSource(1, "owner", "kb", "zh", "v1", "chunk-zh", "北京位于中国。", 0, 7)
    payload = json.dumps({"entities": [
        dict(key="s", name="北京", type="PLACE", aliases=[], anchor=True, qualifier=""),
        dict(key="t", name="中国", type="PLACE", aliases=[], anchor=False, qualifier="")],
        "relations": [dict(source="s", target="t", predicate="位于", quote=source.content, polarity="affirmed", time="")]}, ensure_ascii=False)
    literal = {name: {"edges": len(module.parse_extraction(source, payload, "s7-synthetic").edges),
                     "spans": module._occurrences(source.content, "北京")}
               for name, module in (("baseline", baseline_extract), ("current", extraction))}
    mixed = {name: module.mentioned("请问Franklin bells配套什么设备？", "Franklin bells")
             for name, module in (("baseline", baseline), ("current", retrieval))}
    graph = chain()
    raw = {batch.source.chunk_id: source_row(batch.source) for batch in graph}
    summary = []
    for batch in graph:
        row = source_row(batch.source)
        row.id, row.chunk_type = "summary-" + batch.source.document_id, "summary"
        summary.append(row)
    proof = {}
    for name, module in (("baseline", baseline), ("current", retrieval)):
        proof[name] = await replay_arm(module, graph, CTX, summary, raw,
            "Where is the institute founded by Astrid Vale?", "", enabled=True,
            now=datetime(2026, 10, 19))
    negative = {}
    for name, module in (("baseline", baseline), ("current", retrieval)):
        bad_rows = deepcopy(raw)
        bad_rows[graph[0].source.chunk_id].metadata["source_version"] = "stale-v0"
        try:
            await replay_arm(module, graph, CTX, [], bad_rows, "Astrid Vale", "", enabled=True,
                             now=datetime(2026, 10, 19))
        except PermissionError as error:
            stale = {"rejected": True, "error": str(error)}
        else:
            stale = {"rejected": False}
        negative[name] = {
            "unrelated_query_paths": len(module.find_paths("Unknown person's unrelated question", multi, CTX, tenant_id=1)),
            "reverse_object_paths": len(module.find_paths("Place 0", multi, CTX, tenant_id=1)),
            "identifier_prefix_matched": module.mentioned("NP-2040", "NP-204"),
            "stale_source": stale}
    # A genuinely different predicate wording has no lexical bridge. Record the
    # observed selection, but never call a hash-tie coincidence semantic PASS.
    paraphrase_query = "Where is the institute Astrid Vale established?"
    paraphrase = {"query": paraphrase_query, "intended_fixture_relation": "founded",
                  "status": "UNRESOLVED_LEXICAL_SYNONYM_NOT_IMPLEMENTED"}
    for name, module in (("baseline", baseline), ("current", retrieval)):
        selected = module.find_paths(paraphrase_query, multi, CTX, tenant_id=1, max_paths=1)
        paraphrase[name] = {"first_relation": selected[0].edges[0].predicate if selected else None,
                            "intended_relation_selected": bool(selected and selected[0].edges[0].predicate == "founded"),
                            "path_count": len(selected)}
    return {"classification": "SYNTHETIC_ENGINEERING", "multibranch": cases,
            "cjk_literal": literal, "mixed_script_mention": mixed, "summary_source_identity": proof,
            "negative_controls": negative,
            "predicate_paraphrase": paraphrase,
            "limits": {"outgoing": 12, "paths": 3, "hops": 2}, "new_provider_calls": 0}


async def execute(s6_root):
    baseline, baseline_id = baseline_module("retrieval")
    baseline_extract, extraction_id = baseline_module("extraction")
    real = await reused_real(s6_root, baseline)
    synthetic_results = await synthetic(baseline, baseline_extract)
    passed = (real["baseline_matches_historical_context"] and real["current_matches_historical_context"]
              and all(v["current"]["required_relation_selected"] for v in synthetic_results["multibranch"])
              and synthetic_results["cjk_literal"]["current"]["edges"] == 1
              and synthetic_results["mixed_script_mention"]["current"]
              and synthetic_results["negative_controls"]["current"]["unrelated_query_paths"] == 0
              and synthetic_results["negative_controls"]["current"]["reverse_object_paths"] == 0
              and not synthetic_results["negative_controls"]["current"]["identifier_prefix_matched"]
              and synthetic_results["negative_controls"]["current"]["stale_source"]["rejected"]
              and all(v["identity_retained"] and v["complete_body_in_context"] for v in
                      synthetic_results["summary_source_identity"]["current"]["raw_sources"].values()))
    return {"schema": "s7-graph-offline-check/v1", "status": "OFFLINE_PASS" if passed else "OFFLINE_FAIL",
        "baseline_modules": [baseline_id, extraction_id], "reused_real_response": real,
        "synthetic_engineering": synthetic_results, "new_provider_calls": 0, "new_tokens": 0,
        "frost_romeo": {"status": "NOT_RUN", "reason": "No real source-bound extraction response yet; no synthetic graph substituted."},
        "not_verified": ["current database state", "new Provider wire", "new answer quality", "normal API acceptance"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--s6-root", type=Path, default=Path("E:/WK/run/s6-graphrag-20261019"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(execute(args.s6_root))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output), "new_provider_calls": 0}))
    return 0 if result["status"] == "OFFLINE_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

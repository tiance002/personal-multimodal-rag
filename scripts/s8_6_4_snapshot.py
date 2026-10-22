"""Freeze bounded S8.6.4 retrieval replays from the saved ECS40 extract.

This is an offline artifact extractor. It does not read Gold/scoring data,
connect to a provider, database, service, or ECS, or make network requests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.s8_6_3_offline_evidence import final_wire, state_of
from scripts.s8_6_3_fixed_evidence import digest

SNAPSHOT_SHA256 = "79ecc4f19dd1d08b415270387be8df7003627d8005c14e906c21fc90665943a7"
DEFAULT_SNAPSHOT = Path(
    r"E:\RAG quention\var\reports\s8-6-2-r3\followup40\actual40-final-extracted.json"
)
OUTPUT = ROOT / "tests/fixtures/s8_6_4/retrieval_replays.json"

SOURCE_BUDGET_IDS = {
    "scifact-621": "scifact_621",
    "longbench-zh-dureader:11b3b7afb73f5bf65a51b3083e885a60825b17421c58a828": "longbench_alt_ast_11b3b7",
    "longbench-zh-dureader:5726f5fc6feedda99cafb275a27a8fd67483185b6fb8efa7": "longbench_fallout_5726f5",
    "longbench-zh-dureader:6770736a958b78c4c1b1a0057b34a709bdd869f307f3dc40": "longbench_holiday_6770736",
}
RERANK_LOW_IDS = {
    "mmlongbench-doc-mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0434": "mml_0434",
    "mmlongbench-doc-mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0431": "mml_0431",
    "scifact-1304": "scifact_1304",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def without_parser_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Keep SearchResult fields and metadata except bulky parser/assets payloads."""
    result = json.loads(json.dumps(row, ensure_ascii=False))
    metadata = result.get("metadata")
    removed: list[str] = []
    if isinstance(metadata, dict):
        for key in ("parser_metadata", "assets"):
            if key in metadata:
                metadata.pop(key)
                removed.append(f"metadata.{key}")
    if removed:
        result["_snapshot_omissions"] = {
            "fields": removed,
            "reason": "redundant parser/assets payload omitted; source content and version identity retained",
        }
    return result


def _compact_stage_audit(case: dict[str, Any]) -> list[dict[str, Any]]:
    allowed_fields = {
        "candidate_count", "threshold", "accepted_ids", "mmr_ids", "reason",
        "retrieval_count", "result_count", "selected_ids", "source_unit_count",
    }
    rows = []
    for audit in case.get("strategy_audit", []):
        record = audit.get("record", {})
        stage = record.get("stage")
        if stage not in {"retrieval", "rerank", "merge", "source_unit", "context"}:
            continue
        fields = record.get("fields")
        fields = fields if isinstance(fields, dict) else {}
        rows.append({
            "stage": stage,
            "status": record.get("status"),
            "decision_fields": {k: fields[k] for k in sorted(allowed_fields) if k in fields},
        })
    return rows


def _pg_run(case: dict[str, Any], provider_request_id: str) -> tuple[str, dict[str, Any]]:
    pg = case.get("reconciliation_before_close", {}).get("pg", {})
    prefix = provider_request_id.split("/answer/", 1)[0]
    turns = [row for row in pg.get("turns", []) if row.get("run_id") == prefix]
    if len(turns) != 1:
        raise ValueError(f"SAVED_PG_RUN_NOT_UNIQUE:{case.get('case_id')}:{prefix}:{len(turns)}")
    run_id = turns[0].get("run_id")
    if not isinstance(run_id, str) or run_id != prefix:
        raise ValueError(f"SAVED_PG_RUN_ID_MISMATCH:{case.get('case_id')}")
    return run_id, turns[0]


def _retrieval_case(case: dict[str, Any], case_key: str, category: str) -> dict[str, Any]:
    state = state_of(case)
    wire = final_wire(case)
    request = wire["request"]
    body = request["body"]
    run_id, pg_turn = _pg_run(case, request["request_id"])
    scope = request["scope"]
    if pg_turn.get("knowledge_base_scope") != scope.get("knowledge_base_ids"):
        raise ValueError(f"SAVED_PG_SCOPE_MISMATCH:{case['case_id']}")
    if pg_turn.get("document_scope") != scope.get("document_ids"):
        raise ValueError(f"SAVED_PG_DOCUMENT_SCOPE_MISMATCH:{case['case_id']}")

    search_results = [without_parser_payload(r) for r in state.get("search_results", [])]
    rerank_results = [without_parser_payload(r) for r in state.get("rerank_results", [])]
    raw_rerank = next((row for row in state.get("evidence_trace", [])
                       if row.get("stage") == "rerank_raw"), None)
    if raw_rerank is None:
        raise ValueError(f"RAW_RERANK_TRACE_MISSING:{case['case_id']}")
    input_ids = raw_rerank.get("input_ids", [])
    scores = raw_rerank.get("response", [])
    if not isinstance(input_ids, list) or not isinstance(scores, list):
        raise ValueError(f"RAW_RERANK_TRACE_INVALID:{case['case_id']}")

    message_rows = body.get("messages", [])
    message_text = "\n".join(m.get("content", "") for m in message_rows
                               if isinstance(m.get("content"), str))
    actual_source_ids = [row["id"] for row in search_results
                         if isinstance(row.get("content"), str) and row["content"]
                         and row["content"] in message_text]
    stage_counts = {
        "search_results": len(search_results),
        "rerank_results": len(rerank_results),
        "raw_rerank_input_ids": len(input_ids),
        "raw_rerank_results": len(scores),
        "merge_results": len(state.get("merge_results", [])),
    }
    if stage_counts["raw_rerank_input_ids"] != stage_counts["raw_rerank_results"]:
        raise ValueError(f"RAW_RERANK_CARDINALITY_MISMATCH:{case['case_id']}:{stage_counts}")

    return {
        "case_id": case["case_id"],
        "case_key": case_key,
        "category": category,
        "q0": case["question"],
        "dataset": case.get("dataset"),
        "actual_run_uuid_from_saved_pg": run_id,
        "scope": {
            "knowledge_base_ids": scope.get("knowledge_base_ids", []),
            "document_ids": scope.get("document_ids", []),
            "owner_id": scope.get("owner_id"),
            "saved_pg_turn_scope": {
                "knowledge_base_scope": pg_turn.get("knowledge_base_scope"),
                "document_scope": pg_turn.get("document_scope"),
            },
        },
        "counts": stage_counts,
        "source_search_results": search_results,
        "source_rerank_results": rerank_results,
        "raw_rerank_trace": {"input_ids": input_ids, "results": scores},
        "historical_decision_trace": {
            "query_type": state.get("query_type"),
            "rewrite_query": state.get("rewrite_query"),
            "query_trace": state.get("query_trace"),
            "plan": state.get("plan"),
            "strategy_trace": state.get("strategy_trace"),
            "strategy_coverage": state.get("strategy_coverage"),
            "evidence_trace": [t for t in state.get("evidence_trace", [])
                               if t.get("stage") != "rerank_raw"],
            "stage_audit": _compact_stage_audit(case),
        },
        "actual_final_request": {
            "request_id": request["request_id"],
            "body_sha256": digest(body),
            "model": request.get("model"),
            "message_count": len(message_rows),
            "message_lengths_chars": [len(m.get("content", "")) if isinstance(m.get("content"), str) else None
                                       for m in message_rows],
            "total_message_chars": sum(len(m.get("content", "")) for m in message_rows
                                        if isinstance(m.get("content"), str)),
            "search_chunk_count": len(search_results),
            "rerank_chunk_count": len(rerank_results),
            "raw_rerank_chunk_count": len(input_ids),
            "source_search_ids_observed_in_actual_messages": actual_source_ids,
            "actual_body_source_chunk_count": len(actual_source_ids),
        },
        "source_identity_fields": [
            {k: row.get(k) for k in ("id", "knowledge_base_id", "knowledge_id", "chunk_type",
                 "chunk_index", "start_at", "end_at", "content_revision", "content_rewritten",
                 "sub_chunk_ids", "metadata", "chunk_metadata")}
            for row in search_results
        ],
        "selection_note": "Selection is based on saved case_id/q0 and recorded stages; no Gold or answer included.",
    }


def build(snapshot: Path) -> dict[str, Any]:
    actual_sha = sha256_file(snapshot)
    if actual_sha != SNAPSHOT_SHA256:
        raise ValueError(f"FROZEN_SNAPSHOT_SHA_MISMATCH:{actual_sha}")
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    by_id = {case["case_id"]: case for case in data["cases"]}

    source_ids = dict(SOURCE_BUDGET_IDS)
    # Locate the UTC conversion case by its saved q0, with its actual case ID
    # taken from the frozen snapshot rather than hard-coded from memory.
    utc_cases = [case for case in data["cases"]
                 if isinstance(case.get("question"), str)
                 and "utc" in case["question"].casefold()
                 and case.get("dataset", "").startswith("longbench")]
    if len(utc_cases) != 1:
        raise ValueError(f"UTC_CASE_NOT_UNIQUE:{len(utc_cases)}")
    utc_case = utc_cases[0]
    source_ids[utc_case["case_id"]] = "longbench_utc_conversion_q0_selected"

    missing = (set(source_ids) | set(RERANK_LOW_IDS)) - set(by_id)
    if missing:
        raise ValueError(f"REPLAY_CASES_MISSING:{sorted(missing)}")
    source_cases = [_retrieval_case(by_id[cid], key, "source_budget")
                    for cid, key in source_ids.items()]
    rerank_cases = [_retrieval_case(by_id[cid], key, "rerank_low")
                    for cid, key in RERANK_LOW_IDS.items()]
    if len(source_cases) != 5 or len(rerank_cases) != 3:
        raise ValueError("EXPECTED_5_SOURCE_BUDGET_AND_3_RERANK_LOW_CASES")

    citation_path = ROOT / "tests/fixtures/s8_6_3/ecs40_citation_identity.json"
    citations = json.loads(citation_path.read_text(encoding="utf-8"))["cases"]
    if len(citations) != 3:
        raise ValueError(f"EXPECTED_3_S8_6_3_CITATION_CASES:{len(citations)}")

    return {
        "schema": "s8.6.4-retrieval-replays/v1",
        "source_snapshot": {"path": str(snapshot), "sha256": actual_sha},
        "extraction": "OFFLINE_SAVED_ARTIFACT_ONLY",
        "cases": source_cases + rerank_cases,
        "citation_cases_from_s8_6_3": citations,
        "case_counts": {"source_budget": len(source_cases), "rerank_low": len(rerank_cases),
                        "retrieval_cases_total": len(source_cases) + len(rerank_cases),
                        "citation_cases": len(citations)},
        "input_count_integrity": {
            c["case_id"]: {
                "search_results": c["counts"]["search_results"],
                "raw_rerank_input_ids": c["counts"]["raw_rerank_input_ids"],
                "raw_rerank_results": c["counts"]["raw_rerank_results"],
            }
            for c in source_cases + rerank_cases
        },
        "exclusions": ["answers", "Gold", "scoring/review fields", "parser_metadata", "metadata.assets"],
        "limitations": ["No provider, database, network, ECS, or production action was performed."],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    value = build(args.snapshot)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": value["schema"],
        "snapshot_sha256": value["source_snapshot"]["sha256"],
        "retrieval_cases": value["case_counts"]["retrieval_cases_total"],
        "source_budget": value["case_counts"]["source_budget"],
        "rerank_low": value["case_counts"]["rerank_low"],
        "citation_cases": value["case_counts"]["citation_cases"],
        "utc_case_id": next(c["case_id"] for c in value["cases"]
                             if c["case_key"] == "longbench_utc_conversion_q0_selected"),
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

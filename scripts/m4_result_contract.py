"""Offline M4 result contracts; never sends requests or rewrites frozen results."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping


def execution_status(*, terminal_status: str | None, assistant: Mapping[str, Any] | None,
                     api_error: Any = None, event_count: int = 0,
                     unknown_call_count: int = 0, historical_not_run: bool = False) -> str:
    """Require both successful SSE termination and persisted completion.

    Provider UNKNOWN and historical no-replay decisions take precedence. A failed
    SSE alone is a failed turn, not proof of an UNKNOWN provider outcome.
    """
    if historical_not_run:
        return "NOT_RUN_HISTORICAL_UNKNOWN_REPLAY_PROHIBITED"
    if unknown_call_count:
        return "UNKNOWN"
    if api_error:
        return "UNKNOWN_OR_TRANSPORT_FAILURE" if event_count == 0 else "PARTIAL"
    if assistant and assistant.get("is_completed") is True:
        if terminal_status in ("complete", "completed", "success"):
            return "COMPLETE"
        if terminal_status is None:
            return "PARTIAL"
    return "FAILED"


def row_status(row: Mapping[str, Any]) -> str:
    return execution_status(
        terminal_status=row.get("terminal_status"), assistant=row.get("assistant_message"),
        api_error=row.get("api_error"), event_count=row.get("sse_event_count", 0),
        unknown_call_count=max(row.get("unknown_call_count", 0), sum(
            c.get("outcome") == "UNKNOWN" for c in row.get("provider_calls", []))),
        historical_not_run=row.get("status") == "NOT_RUN_HISTORICAL_UNKNOWN_REPLAY_PROHIBITED")


def audit_bindings(case: Mapping[str, Any]) -> dict[str, Any]:
    """Check identity consistency only; this does not validate qrel source content."""
    positive = {s for s, grade in (case.get("qrels") or {}).items() if float(grade) > 0}
    bindings = case.get("qrel_source_to_document") or {}
    inverse = case.get("document_to_source") or {}
    expected = {x["source_id"]: x["id"] for x in case.get("expected_documents", [])
                if x.get("source_id") and x.get("id")}
    conflicts = [s for s in positive if bindings.get(s) and
                 (inverse.get(bindings[s]) != s or expected.get(s) != bindings[s])]
    counts = Counter(bindings.get(s) for s in positive if bindings.get(s))
    duplicates = [d for d, n in counts.items() if n > 1]
    missing = sorted(s for s in positive if not bindings.get(s))
    return {"positive_qrel_count": len(positive), "unbound_sources": missing,
            "conflicting_sources": sorted(conflicts), "duplicate_document_targets": duplicates,
            "identity_consistent": not (missing or conflicts or duplicates),
            "content_projection_status": "NOT_VALIDATED",
            "scope": "Frozen identity cross-check only; qrel relevance and file content require separate audit"}


def source_coverage(required: Iterable[str], retrieved: Iterable[str]) -> dict[str, Any]:
    required, retrieved = set(required), set(retrieved)
    hits = required & retrieved
    return {"required_source_count": len(required), "hit_source_count": len(hits),
            "any_source_hit": bool(hits) if required else None,
            "all_required_sources_hit": required <= retrieved if required else None,
            "required_source_recall": len(hits) / len(required) if required else None}


def aggregate_coverage(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [r for r in rows if r.get("required_source_count", 0) > 0]
    n = len(rows)
    any_hits = sum(r["any_source_hit"] is True for r in rows)
    all_hits = sum(r["all_required_sources_hit"] is True for r in rows)
    return {"qid_denominator": n, "any_source_hit_count": any_hits,
            "any_source_hit_rate": any_hits / n if n else None,
            "all_required_sources_hit_count": all_hits,
            "all_required_sources_hit_rate": all_hits / n if n else None,
            "macro_required_source_recall": sum(r["required_source_recall"] for r in rows) / n if n else None,
            "scope": "Source identity only; no supporting-sentence or joint answer metric"}


def main() -> int:
    import argparse
    import hashlib
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.resolve() in (args.raw.resolve(), args.cases.resolve()):
        parser.error("output must not overwrite frozen input")
    raw_bytes, case_bytes = args.raw.read_bytes(), args.cases.read_bytes()
    rows = [json.loads(line) for line in raw_bytes.decode("utf-8-sig").splitlines() if line.strip()]
    cases = {c["case_id"]: c for c in json.loads(case_bytes.decode("utf-8-sig"))}
    statuses, audits, hotpot = [], [], []
    for row in rows:
        case = cases[row["case_id"]]
        statuses.append({"case_id": row["case_id"], "raw_status": row.get("status"),
                         "derived_status": row_status(row)})
        audits.append({"case_id": row["case_id"], **audit_bindings(case)})
        if row.get("dataset") != "hotpotqa":
            continue
        cap = row.get("stage_capture") or {}
        states = {s["stage"]: s["state"] for s in cap.get("stages", [])
                  if s.get("status") == "completed" and isinstance(s.get("state"), dict)}
        required = [s for s, grade in case["qrels"].items() if float(grade) > 0]
        source_map = case.get("document_to_source") or {}
        stages, by_id = {}, {}
        for stage, key in (("search", "search_results"), ("rerank", "rerank_results"), ("merge", "merge_results")):
            state_key = "retrieval" if stage == "search" else stage
            found = states.get(state_key, {}).get(key, [])
            by_id.update({x["id"]: x for x in found if x.get("id")})
            source_ids = [source_map.get(x.get("knowledge_id") or x.get("document_id")) for x in found]
            stages[stage] = {"observed": state_key in states, **source_coverage(required, source_ids)}
        final = cap.get("finalized_context") or {}
        selected = [by_id[i] for i in final.get("result_ids", []) if i in by_id]
        stages["context"] = {"observed": "result_ids" in final,
                             **source_coverage(required, [source_map.get(x.get("knowledge_id") or x.get("document_id")) for x in selected]),
                             "unresolved_result_ids": [i for i in final.get("result_ids", []) if i not in by_id]}
        hotpot.append({"case_id": row["case_id"], "derived_status": row_status(row), "stages": stages})
    summaries = {}
    for stage in ("search", "rerank", "merge", "context"):
        observed = [r["stages"][stage] for r in hotpot if r["stages"][stage]["observed"]]
        complete = [r["stages"][stage] for r in hotpot if r["derived_status"] == "COMPLETE" and r["stages"][stage]["observed"]]
        summaries[stage] = {"frozen_qid_denominator": len(hotpot),
                            "unobserved_qids": len(hotpot) - len(observed),
                            "observed_stage_metrics": aggregate_coverage(observed),
                            "completed_observed_stage_metrics": aggregate_coverage(complete)}
    result = {"schema": "m4-s1-metric-tools/v1", "status": "OFFLINE_PASS",
              "source_sha256": {str(args.raw): hashlib.sha256(raw_bytes).hexdigest(),
                                str(args.cases): hashlib.sha256(case_bytes).hexdigest()},
              "functions": ["execution_status", "row_status", "audit_bindings", "source_coverage", "aggregate_coverage"],
              "root_cause": {"file": "E:/WK/run/m4-full-sweep-20261018/run_api_sweep.py", "function": "run",
                             "defect": "completed/success omitted actual SSE terminal complete",
                             "fix": "calls execution_status; UNKNOWN precedence retained"},
              "status_counts": dict(Counter(r["derived_status"] for r in statuses)),
              "status_delta": statuses, "binding_audits": audits,
              "hotpotqa": {"stage_metrics": summaries, "cases": hotpot},
              "limitations": ["Identity consistency does not establish qrel relevance or source content alignment",
                              "Pew/MMlongbench mapping remains NOT_VALIDATED pending native row/document/page audit",
                              "No supporting sentence or joint score computed; no cloud request or historical row rewritten"]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out), "status_counts": result["status_counts"], "hotpotqa": summaries}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

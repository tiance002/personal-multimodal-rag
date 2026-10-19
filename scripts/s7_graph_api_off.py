"""Offline Graph-off inputs from the exact completed S7 normal API preparation.

No original question is submitted again. No Provider/DB client is constructed.
An off arm is sendable only after the real on-arm generation wire has been
reproduced exactly through the formal renderer and Provider body builder.
Missing data is NOT_REPLAYABLE, never an approximate preparation.
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
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT, Evidence
from backend.weknora_port.models.catalog import resolve_config
from backend.weknora_port.models.output_contract import resolve_output_contract
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget
from backend.weknora_port.retrieval.types import SearchResult
from scripts.s7_graph_generation import QWEN, QWEN_ID, prepare_case


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(value).hexdigest()


def file_id(path):
    return {"path": str(path), "sha256": sha(Path(path).read_bytes())}


def exact_one(values, code):
    if len(values) != 1:
        raise ValueError(code)
    return values[0]


def completed_state(capture, stage):
    return exact_one([item for item in capture["stages"] if item["stage"] == stage and
                      item["status"] == "completed"], "EXACT_SAVED_STAGE_REQUIRED:" + stage)["state"]


def ordinary_after_top_k(capture):
    """Fusion appends graph rows; recover unchanged ordinary rows, not re-rank."""
    merged = completed_state(capture, "merge")
    fused = completed_state(capture, "graph_fusion")
    by_id = {row["id"]: row for row in merged["merge_results"]}
    ordinary = [row for row in fused["merge_results"] if row["match_type"] != "graph"]
    if any(row != by_id.get(row["id"]) for row in ordinary):
        raise ValueError("ORDINARY_POST_TOP_K_NOT_PROVABLE")
    if not ordinary:
        raise ValueError("NATIVE_SEARCH_NOTHING_FALLBACK_PREPARATION_NOT_CAPTURED")
    return ordinary, fused


def arm(finalized, rows, now, *, language="Chinese (Simplified)"):
    return {"finalized_context": finalized, "search_results": rows,
            "evidence": [asdict(Evidence.from_result(SearchResult(**row))) for row in rows],
            "history": [], "language": language, "rendered_at": now.isoformat(),
            "system_prompt": DEFAULT_SYSTEM_PROMPT, "is_fallback": False}


async def api_case(case, directory, plan):
    directory = Path(directory)
    ledger_path = directory / "execution.json"
    if not ledger_path.exists():
        ledger_path = directory.parent / "execution.json"
    ledger = read(ledger_path)
    if (ledger["tenant_id"] != plan["tenant_id"] or ledger["tenant_id"] != 332 or
            hashlib.sha256(ledger["subject_id"].encode()).hexdigest() != plan["subject_sha256"] or
            ledger["allowed_kbs"] != [plan["knowledge_base_id"]] or
            any(item["status"] != "SETTLED" for item in ledger["entries"])):
        raise ValueError("ACTUAL_API_LEDGER_SCOPE_OR_COMPLETION_MISMATCH")
    # LocalExecution's existing durable capture directory (not a new sink).
    evidence_dir = ledger_path.parent / "evidence"
    stage_path = exact_one(list(evidence_dir.glob("*.stages.json")), "EXACTLY_ONE_API_STAGE_CAPTURE_REQUIRED")
    capture = read(stage_path)
    ordinary, fused = ordinary_after_top_k(capture)
    context_event = exact_one([item for item in capture["stages"] if item["stage"] == "context" and
                              item["status"] == "completed"], "COMPLETED_CONTEXT_REQUIRED")
    now = datetime.fromtimestamp(context_event["time"])
    if fused["query"] != case["request"]["content"]:
        raise ValueError("ORIGINAL_API_QUESTION_CHANGED")
    documents = {document["id"]: document for document in plan["corpus_originals"]}
    for row in [*completed_state(capture, "retrieval")["search_results"], *fused["merge_results"]]:
        document = documents.get(row["knowledge_id"])
        if (document is None or row["knowledge_base_id"] != plan["knowledge_base_id"] or
                row["metadata"].get("source_version") != document["source_version"] or
                not row.get("is_enabled", True)):
            raise ValueError("ACTUAL_API_SOURCE_NOT_IN_FROZEN_VERSION_POOL")
    expected_scope = {"owner_id": ledger["subject_id"], "knowledge_base_ids": [plan["knowledge_base_id"]],
                      "document_ids": case["request"]["document_scope"]}
    requests = []
    rerank_receipts = []
    for path in evidence_dir.glob("*.request.json"):
        raw = read(path)
        if raw["request_id"].endswith("/answer/1/cheap"):
            requests.append((path, raw))
        elif raw.get("capability") == "rerank":
            receipt = exact_one([row for row in ledger["entries"] if row["request_id"] == raw["request_id"]],
                                "ACTUAL_BGE_LEDGER_RECEIPT_REQUIRED")
            response_path = path.with_name(path.name.replace(".request.json", ".response.body"))
            if raw["scope"] != expected_scope or receipt["wire_sha256"] != sha(raw["body"]):
                raise ValueError("ACTUAL_BGE_SCOPE_OR_WIRE_SHA_MISMATCH")
            rerank_receipts.append({"request_id": raw["request_id"], "body": raw["body"],
                "response": read(response_path), "files": [file_id(path), file_id(response_path)],
                "wire_sha256": receipt["wire_sha256"]})
    wire_path, raw = exact_one(requests, "EXACTLY_ONE_FIRST_QWEN_GENERATION_REQUIRED")
    if (raw["provider"] != "siliconflow" or raw["model"] != QWEN or raw["model_id"] != QWEN_ID or
            raw["scope"] != expected_scope):
        raise ValueError("ACTUAL_QWEN_OR_SCOPE_MISMATCH")
    body = raw["body"]
    if (body.get("enable_thinking") is not False or body.get("max_tokens") != 1024 or
            body.get("temperature") != .3 or not isinstance(body.get("stream"), bool)):
        raise ValueError("ACTUAL_API_GENERATION_PARAMETERS_NOT_SUPPORTED_BY_FIXED_PLAN")
    receipt = exact_one([row for row in ledger["entries"] if row["request_id"] == raw["request_id"]], "ACTUAL_GENERATION_LEDGER_RECEIPT_REQUIRED")
    if receipt["wire_sha256"] != sha(body):
        raise ValueError("ACTUAL_PROVIDER_WIRE_SHA_MISMATCH")
    # Reconstruct from formal source DTOs; never copy an on wire wholesale into
    # a different context. Exact comparison rejects missing history/memory,
    # language or custom settings rather than guessing their values.
    actual_final = capture["finalized_context"]
    on = arm(actual_final, fused["merge_results"], now)
    candidate = {"id": case["id"], "question": fused["query"], "arms": {"on": on}}
    request, prepared = prepare_case(candidate, "on")
    config = resolve_config("siliconflow", QWEN, model_id=QWEN_ID)
    model = ProviderModel(config, None)
    rebuilt = model.build_request_body(prepared.messages, request.options, stream=body["stream"])
    if rebuilt != body:
        raise ValueError("ON_WIRE_NOT_REPLAYABLE_HISTORY_MEMORY_OR_CONFIGURATION_MISSING")
    if resolve_output_contract(config, body, capability="chat").output_upper != 1024:
        raise ValueError("ACTUAL_QWEN_OUTPUT_BOUND_UNVERIFIED")
    contexts = actual_final["rendered_contexts"]
    if not contexts or actual_final["user_content"].count(contexts) != 1:
        raise ValueError("FROZEN_CONTEXT_TEMPLATE_NOT_RECOVERABLE")
    # Freeze every byte outside rendered evidence, including original request
    # and metadata date. Only the evidence placeholder is rendered again.
    template = actual_final["user_content"].replace(contexts, "{{contexts}}")
    context = CallContext("s7-offline-api-off-no-send", TrustedScope(ledger["subject_id"],
                          (plan["knowledge_base_id"],), tuple(case["request"]["document_scope"])))
    state = PipelineState(query=fused["query"], rewrite_query=fused["rewrite_query"],
        targets=(SearchTarget(plan["knowledge_base_id"]),), merge_results=[SearchResult(**row) for row in ordinary],
        context_template=template, language=on["language"], enable_rewrite=False, enable_expansion=False)
    finalized = await into_chat_message(state, context, now=now)
    off = arm(asdict(finalized), ordinary, now)
    candidate["arms"]["off"] = off
    prepare_case(candidate, "off")
    return {"id": case["id"], "question": fused["query"], "query": fused["query"],
        "rewrite": fused["rewrite_query"], "scope": {"tenant_id": 332, **expected_scope},
        "classification": "EXACT_NEW_API_PREPARATION_GRAPH_OFF_FIXED_CONTEXT_NOT_NEW_API",
        "scoring_only": case["scoring_only"], "arms": {"off": off},
        "on_preparation_proof": {"provider_body_reconstructed_exactly": True,
            "wire_sha256": sha(body), "original_request_id": raw["request_id"],
            "finalized_context": actual_final, "graph_trace": fused["graph_trace"],
            "ordinary_post_top_k_rule": "Actual fusion-completed rows excluding graph additions, identical to saved merge rows"},
        "frozen_stages": {"search": completed_state(capture, "retrieval")["search_results"],
            "rerank": completed_state(capture, "rerank")["rerank_results"],
            "ordinary_post_top_k": ordinary, "raw_bge_receipts": rerank_receipts},
        "files": [file_id(ledger_path), file_id(stage_path), file_id(wire_path)], "new_provider_calls": 0}


async def execute(workdir):
    plan = read(workdir / "graph-plan.json")
    cases, missing = [], []
    for case in plan["cases"]:
        if case.get("planned_validation") != "NORMAL_API_ON_THEN_FIXED_OFF_FROM_SAME_ACTUAL_PREPARATION":
            continue
        directory = workdir / "graph-api" / case["id"]
        try:
            result = await api_case(case, directory, plan)
        except (KeyError, TypeError, ValueError, FileNotFoundError) as error:
            missing.append({"id": case["id"], "status": "NOT_REPLAYABLE", "reason": str(error)})
        else:
            cases.append(result)
    return {"schema": "s7-graph-contrasts/v1", "status": "PREPARED_NOT_NEW_GENERATION" if len(cases) == 2 else "NOT_REPLAYABLE",
        "generation_parameters": {"thinking": False, "max_completion_tokens": 1024, "temperature": .3},
        "cases": cases, "not_replayable": missing, "new_provider_calls": 0, "new_tokens": 0,
        "database_access": False, "limitations": ["Graph-off is a fixed preparation diagnostic, not another normal API run",
            "No unknown request is resent; no query understanding, Search, Rerank or generation is called"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, default=Path("E:/WK/run/s7-20261019"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(execute(args.workdir))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "prepared": len(result["cases"]), "not_replayable": result["not_replayable"], "new_provider_calls": 0}))
    return 0 if result["status"] == "PREPARED_NOT_NEW_GENERATION" else 1


if __name__ == "__main__":
    raise SystemExit(main())

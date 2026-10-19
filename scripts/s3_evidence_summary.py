"""Read saved S3 evidence once; never contact a service or alter source evidence.

Expected facts are loaded only after observed QA rows have been assembled. Literal
fact presence is an audit aid, not semantic grading or proof of source provenance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def load_json(path, warnings, *, optional=False):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        if not optional or path.exists():
            warnings.append({"file": str(path), "status": "UNVERIFIED", "reason": type(error).__name__})
        return None


def facts(rows):
    return [{key: row[key] for key in ("id", "knowledge_id", "knowledge_base_id", "chunk_type", "score", "content") if key in row}
            for row in rows if isinstance(row, dict)]


def response_summary(path, warnings):
    try:
        raw = path.read_bytes()
    except OSError as error:
        return {"status": "IN_PROGRESS", "reason": type(error).__name__}
    text = raw.decode("utf-8", errors="replace")
    packets, parse_errors = [], 0
    if text.lstrip().startswith("data:") or "\ndata:" in text:
        for line in text.splitlines():
            if not line.startswith("data:"): continue
            data = line[5:].strip()
            if data == "[DONE]": continue
            try: packets.append(json.loads(data))
            except ValueError: parse_errors += 1
    elif text.strip():
        try: packets.append(json.loads(text))
        except ValueError: parse_errors += 1
    models, finishes, pieces, usage = [], [], [], None
    reasoning_characters, reasoning_deltas = 0, 0
    for packet in packets:
        if not isinstance(packet, dict): continue
        if packet.get("model") and packet["model"] not in models: models.append(packet["model"])
        if isinstance(packet.get("usage"), dict): usage = packet["usage"]
        for choice in packet.get("choices", []):
            if choice.get("index", 0) != 0: continue
            if choice.get("finish_reason") is not None: finishes.append(choice["finish_reason"])
            message = choice.get("delta", choice.get("message", {}))
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str): pieces.append(content)
            reasoning = message.get("reasoning_content") if isinstance(message, dict) else None
            if isinstance(reasoning, str) and reasoning:
                reasoning_characters += len(reasoning)
                reasoning_deltas += 1
    if parse_errors:
        warnings.append({"file": str(path), "status": "IN_PROGRESS", "reason": "partial_or_invalid_response_json", "invalid_records": parse_errors})
    return {"raw_file": str(path), "raw_bytes": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "response_models": models, "finish_reasons": finishes, "usage": usage,
            "answer": "".join(pieces), "answer_content_characters": sum(map(len, pieces)),
            "reasoning_content_characters": reasoning_characters, "reasoning_delta_count": reasoning_deltas,
            "finish_reason": finishes[-1] if finishes else None, "parse_status": "UNVERIFIED" if parse_errors else "PARSED" if packets else "IN_PROGRESS"}


def compact_usage(usage):
    if not isinstance(usage, dict): return None
    fields = ("status", "prompt_tokens", "completion_tokens", "total_tokens", "cache_status")
    result = {key: usage[key] for key in fields if key in usage}
    provider = usage.get("provider_usage_facts") or {}
    result["observed_total_lower_bound"] = provider.get("observed_lower_bound")
    result["reasoning_tokens"] = next((row[1].get("value") for row in provider.get("fields", [])
                                       if row[0] == "reasoning" and row[1].get("valid")), None)
    return result


def call_summary(path, request, db_calls, warnings):
    prefix = path.name.removesuffix(".request.json")
    sidecar = lambda suffix: path.with_name(prefix + suffix)
    response = response_summary(sidecar(".response.body"), warnings)
    metadata = load_json(sidecar(".response.json"), warnings, optional=True)
    timing = load_json(sidecar(".timing.json"), warnings, optional=True) or {}
    ledger = next((row for row in db_calls if row.get("request_id") == request.get("request_id")
                   and row.get("capability") == request.get("capability")
                   and row.get("wire_sha256") == request.get("wire_sha256")), None)
    start, end = timing.get("transport_open_started_monotonic"), timing.get("read_finished_monotonic")
    duration = end - start if isinstance(start, (float, int)) and isinstance(end, (float, int)) else None
    finished = bool(ledger and ledger.get("finished_at"))
    terminal_unknown = finished and ledger.get("outcome") == "UNKNOWN"
    settled = finished and ledger.get("outcome") == "RESPONSE_RECEIVED"
    usage = compact_usage(ledger.get("usage")) if ledger else None
    return {"request_file": str(path), **{key: request.get(key) for key in
                ("request_id", "provider", "model", "model_id", "capability", "wire_sha256", "wire_bytes")},
            "capture_complete": metadata.get("complete") if metadata else None,
            "http_status": metadata.get("status_code") if metadata else None,
            "transport_seconds": duration, "timing": timing,
            "ledger_outcome_at_snapshot": ledger.get("outcome") if ledger else None,
            "ledger_usage_at_snapshot": usage,
            "ledger_completion_state": "TERMINAL_UNKNOWN" if terminal_unknown else "FINISHED" if finished else "IN_PROGRESS" if ledger else "UNVERIFIED",
            "settled_provider_usage": usage if settled and usage and usage.get("status") == "REPORTED" else None,
            "unknown_observed_usage_lower_bound": usage if terminal_unknown else None,
            "usage_meaning": "Settled usage and terminal UNKNOWN observed lower bounds are separate; unknown final consumption is not zero.",
            "ledger_finished_at": ledger.get("finished_at") if ledger else None,
            "ledger_note": "Snapshot observation; UNKNOWN on an unfinished call is not a terminal outcome.",
            **response}


def context_channels(rendered):
    channels, channel = {"image_caption": [], "image_text_ocr_label": []}, None
    for line in rendered.splitlines():
        if line.startswith("> **Image caption:**"):
            channel = "image_caption"
            channels[channel].append(line.removeprefix("> **Image caption:**").strip())
        elif line.startswith("> **Image text (OCR):**"):
            channel = "image_text_ocr_label"
            channels[channel].append(line.removeprefix("> **Image text (OCR):**").strip())
        elif line.startswith(">") and channel:
            channels[channel].append(line[1:].lstrip())
        elif line.strip(): channel = None
    return {"meaning": "Exact labeled excerpts from final context; labels alone do not establish original OCR provenance.",
            **{key: "\n".join(lines).strip() for key, lines in channels.items()}}


def summarize(root):
    warnings = []
    db_path = root / "db-current.json"
    db = load_json(db_path, warnings) or {}
    stages = {}
    for path in sorted((root / "evidence").glob("*.stages.json")):
        row = load_json(path, warnings)
        if isinstance(row, dict) and row.get("run_id"): stages[row["run_id"]] = (path, row)
    requests = []
    for path in sorted((root / "evidence").glob("*.request.json")):
        row = load_json(path, warnings)
        if isinstance(row, dict): requests.append((path, row))
    turns = {turn["run_id"]: turn for turn in db.get("turns", []) if turn.get("run_id")}
    chunks = {row["id"]: row for row in db.get("chunks", []) if row.get("id")}
    history_rows = list(db.get("messages", []))
    for path in sorted(root.glob("*.history.json")):
        saved = load_json(path, warnings)
        data = saved.get("data", saved) if isinstance(saved, dict) else saved
        rows = data.get("messages", []) if isinstance(data, dict) else data
        if isinstance(rows, list):
            history_rows.extend({**row, "_history_file": str(path)} for row in rows if isinstance(row, dict))
    cases = []
    for run_id in sorted(set(stages) | set(turns)):
        turn = turns.get(run_id, {})
        stage_path, pipeline = stages.get(run_id, (None, {}))
        completed = {row["stage"]: row for row in pipeline.get("stages", []) if row.get("status") == "completed"}
        question = turn.get("q0") or next((row.get("state", {}).get("query") for row in completed.values() if row.get("state", {}).get("query")), None)
        final = pipeline.get("finalized_context") or {}
        context_state = completed.get("context", {}).get("state", {})
        candidate_rows = {row["id"]: row for entry in completed.values() for field in
                          ("search_results", "rerank_results", "merge_results") for row in entry.get("state", {}).get(field, []) if isinstance(row, dict) and row.get("id")}
        selected = [chunks.get(identity) or candidate_rows.get(identity) or {"id": identity} for identity in final.get("result_ids", [])]
        local_calls = [call_summary(path, row, db.get("calls", []), warnings) for path, row in requests
                       if row.get("request_id", "").startswith(run_id + "/")]
        local_calls.sort(key=lambda row: row.get("timing", {}).get("transport_open_started_monotonic") or 0)
        answers = [row for row in local_calls if "/answer/" in row["request_id"]]
        latest = answers[-1] if answers else None
        persisted = next((row for row in history_rows if row.get("id") == turn.get("assistant_message_id")), None)
        matched_call = next((row for row in reversed(answers) if persisted is not None and row.get("answer") == persisted.get("content")), None)
        chosen = matched_call or latest
        answer = chosen.get("answer") if chosen else None
        terminal = turn.get("status") in ("completed", "failed", "cancelled", "error") or bool(persisted and persisted.get("is_completed"))
        cancelled = turn.get("error_code") == "MODEL_CALL_CANCELLED" or bool(persisted and persisted.get("turn_status") == "cancelled")
        terminal_unknown = any(row["ledger_completion_state"] == "TERMINAL_UNKNOWN" for row in local_calls)
        complete = bool(chosen and chosen.get("capture_complete") is True)
        match = bool(matched_call) if persisted is not None and answer is not None and (complete or terminal) else None
        if cancelled:
            status = "TERMINAL_CANCELLED_UNKNOWN" if terminal_unknown else "TERMINAL_CANCELLED"
        elif terminal_unknown:
            status = "TERMINAL_UNKNOWN"
        elif terminal and turn.get("status") in ("failed", "error"):
            status = "TERMINAL_FAILED"
        else:
            status = "IN_PROGRESS" if not terminal else "VERIFIED" if match and complete else "MISMATCH" if match is False else "UNVERIFIED"
        settled = [row["settled_provider_usage"] for row in local_calls if row.get("settled_provider_usage")]
        settled_totals = {name: sum(row[name] for row in settled) if settled and all(type(row.get(name)) is int for row in settled) else None
                          for name in ("prompt_tokens", "completion_tokens", "total_tokens")}
        unknown_calls = [{"request_id": row["request_id"], "finished_at": row["ledger_finished_at"],
                          "observed_lower_bound": row["unknown_observed_usage_lower_bound"]} for row in local_calls if row.get("unknown_observed_usage_lower_bound")]
        answer_requests = [request for _, request in requests if chosen and request.get("request_id") == chosen["request_id"]]
        delivered = any(message.get("content") == final.get("user_content") for request in answer_requests
                        for message in request.get("body", {}).get("messages", []) if isinstance(message, dict)) if final.get("user_content") else None
        stage_report = {}
        for name, event, field in (("Search", "retrieval", "search_results"), ("Rerank", "rerank", "rerank_results"), ("Merge", "merge", "merge_results")):
            record = completed.get(event)
            rows = record.get("state", {}).get(field) if record else None
            stage_report[name] = {"status": "OBSERVED" if isinstance(rows, list) else "UNVERIFIED",
                "count": sum(row is not None for row in rows) if isinstance(rows, list) else None,
                "event_fields": record.get("fields") if record else None, "facts": facts(rows or [])}
        post_topk = next((context_state.get(name) for name in ("merge_results", "rerank_results", "search_results") if context_state.get(name)), None)
        stage_report["TopK"] = {"status": "OBSERVED_AFTER_TOPK_AT_CONTEXT" if post_topk is not None else "UNVERIFIED",
            "note": "No dedicated TopK snapshot. Context snapshot follows TopK and optional source-unit expansion; it cannot isolate their individual effects.",
            "count": sum(row is not None for row in post_topk) if post_topk is not None else None, "facts": facts(post_topk or [])}
        stage_report["Context"] = {"status": "OBSERVED" if final else "UNVERIFIED", "count": len(final["result_ids"]) if "result_ids" in final else None,
            "result_ids": final.get("result_ids"), "selected_chunk_facts": facts(selected),
            "selected_chunk_types": [row.get("chunk_type") for row in selected],
            "rendered_contexts": final.get("rendered_contexts"), "user_content": final.get("user_content"),
            "labeled_image_facts": context_channels(final.get("rendered_contexts", ""))}
        cases.append({"status": status, "question": question, "run_id": run_id,
            "session_id": turn.get("session_id") or pipeline.get("session_id"),
            "turn_status_at_snapshot": turn.get("status"), "turn_error_code": turn.get("error_code"),
            "stages_file": str(stage_path) if stage_path else None, "stages": stage_report, "calls": local_calls,
            "answer": answer, "answer_selection": "matched_to_saved_assistant_message" if matched_call else "last_observed_answer_attempt_not_verified_as_final",
            "provider_answer_complete": complete,
            "final_context_exactly_present_in_answer_request": delivered,
            "earliest_observed_failure_layer": "answer_generation" if terminal and delivered and chosen and not answer else None,
            "usage_accounting": {"settled_call_count": len(settled), "settled_provider_tokens": settled_totals,
                "terminal_unknown_calls": unknown_calls, "final_unknown_consumption": None},
            "persistence": {"status": "MATCH" if match else "MISMATCH" if match is False else "UNVERIFIED",
                "assistant_message_id": turn.get("assistant_message_id"), "saved_content": persisted.get("content") if persisted else None,
                "history_file": persisted.get("_history_file") if persisted else None,
                "saved_turn_status": persisted.get("turn_status") if persisted else None,
                "saved_error_code": persisted.get("turn_error_code") if persisted else None,
                "saved_is_completed": persisted.get("is_completed") if persisted else None,
                "comparison_scope": "observed_body_only_incomplete_capture" if not complete else "complete_provider_body",
                "empty_body_match_is_not_answer_success": match is True and not answer,
                "reason": None if match is not None else "Need terminal turn, complete answer capture and saved assistant message to verify."}})
    # Post-hoc expected facts cannot influence the preceding observed-evidence extraction.
    expected_path = root / "questions_expected.json"
    if not expected_path.exists(): expected_path = root / "samples" / "questions_expected.json"
    expected = load_json(expected_path, warnings, optional=True) or {}
    questions = expected.get("questions", [])
    for case in cases:
        gold = next((row for row in questions if row.get("question") == case["question"]), None)
        if not gold:
            case["posthoc_literal_fact_check"] = {"status": "UNVERIFIED", "reason": "No exact question match in expected file."}
            continue
        wanted = gold.get("expected")
        tokens = list(wanted.values()) if isinstance(wanted, dict) else wanted if isinstance(wanted, list) else [wanted]
        checks = [{"expected_literal": token, "present": bool(re.search(r"(?<!\w)" + re.escape(str(token)) + r"(?!\w)", case.get("answer") or "", re.I))} for token in tokens]
        case["question_id"] = gold.get("id")
        case["posthoc_literal_fact_check"] = {"status": "NO_ANSWER_TERMINAL" if case["status"].startswith("TERMINAL_") and not case.get("answer") else "UNVERIFIED" if not case["provider_answer_complete"] else "ALL_LITERAL_FACTS_PRESENT" if all(row["present"] for row in checks) else "LITERAL_FACTS_MISSING",
            "meaning": "Literal presence only; no semantic grade or original-image/OCR provenance claim.", "checks": checks, "expected_file": str(expected_path)}
    return {"schema_version": "s3-evidence-summary/v1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "saved_evidence_only_no_service_calls", "db_snapshot_file": str(db_path), "db_observed_at": db.get("observed_at"),
        "cases": cases, "warnings": warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=Path("E:/WK/run/s3-product-20261019"))
    args = parser.parse_args()
    result = summarize(args.run_dir)
    output = args.run_dir / "qa-summaries.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "cases": [{"run_id": row["run_id"], "status": row["status"], "question_id": row.get("question_id")} for row in result["cases"]], "warnings": len(result["warnings"])}, ensure_ascii=True))


if __name__ == "__main__":
    main()

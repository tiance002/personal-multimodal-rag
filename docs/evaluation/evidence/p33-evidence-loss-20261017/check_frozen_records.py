"""Read-only consistency checks for the frozen P32 evidence used by P33."""
from __future__ import annotations

import json
from pathlib import Path


EVIDENCE = Path(__file__).resolve().parent
P32_RUN = Path(r"E:\WK\run\p32-20261017")


def main() -> int:
    trace = json.loads((EVIDENCE / "frozen-run-trace-summary.json").read_text(encoding="utf-8"))
    database = json.loads((EVIDENCE / "omni-db-readonly.json").read_text(encoding="utf-8"))
    final_verification = json.loads((P32_RUN / "final-verification.json").read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in trace["cases"]}
    prepared = {case["case_id"]: case for case in final_verification["prepared_vs_wire_checks"]}
    checks: list[dict[str, str]] = []

    def check(name: str, condition: bool) -> None:
        if not condition:
            raise AssertionError(name)
        checks.append({"check": name, "result": "PASS"})

    check("baseline_matches_P32", trace["baseline_sha"] == "0a956fa6540177fd1d42904ae528d81dc3424739")
    check("P33_provider_calls_and_tokens_are_zero", trace["real_provider_calls_this_task"] == 0 and trace["new_token_usage"] == 0)

    for case_id in ("mml-0434", "hotpot-5a7631", "omni-page007", "mml-0959"):
        case = cases[case_id]
        mapping = case["rerank"]["required_candidate_provider_input_indices"][0]
        check(f"{case_id}_has_30_search_and_rerank_candidates", case["search"]["candidate_count"] == 30 and case["rerank"]["input_count"] == 30)
        check(f"{case_id}_target_maps_to_search_rank_minus_one", mapping["index"] == case["search"]["required_candidate"]["rank"] - 1 and mapping["required_phrase_present"])
        check(f"{case_id}_embedding_text_equals_rerank_query", case["query"]["embedding_text_equals_rerank_query"])
        check(f"{case_id}_prepared_provider_messages_verified", prepared[case_id]["wire_matches_prepared_role_and_content"])

    check(
        "mml0434_lost_at_empty_rerank_before_merge",
        cases["mml-0434"]["rerank"]["selected_count_in_stage"] == 0
        and not cases["mml-0434"]["merge"]["stage_present"]
        and cases["mml-0434"]["top_k_context"]["captured_result_ids"] == [],
    )
    hotpot = cases["hotpot-5a7631"]
    check("hotpot_location_chunk_not_in_rerank_merge_context", not hotpot["merge"]["target_retained"] and not hotpot["top_k_context"]["target_id_in_context"])
    check(
        "hotpot_merge_ids_equal_context_ids",
        hotpot["merge"]["output_ids"] == hotpot["top_k_context"]["context_stage_ids"] == hotpot["top_k_context"]["captured_result_ids"],
    )

    ocr = next(chunk for chunk in database["chunks"] if chunk["id"] == "51316a20-67fd-4732-b520-c049999933d0")
    check(
        "omni_ocr_is_enabled_ready_and_indexed_in_same_version",
        ocr["chunk_type"] == "image_ocr"
        and ocr["is_enabled"]
        and ocr["index_status"] == "ready"
        and ocr["embedding_enabled"]
        and ocr["metadata"]["source_version"] == "35ad1226-ec0e-4931-a87d-257e756f525c",
    )
    omni = cases["omni-page007"]
    check("omni_merge_ids_equal_context_ids", omni["merge"]["output_ids"] == omni["top_k_context"]["context_stage_ids"] == omni["top_k_context"]["captured_result_ids"])
    check("omni_required_ocr_chunk_absent_from_final_context", not omni["top_k_context"]["target_id_in_context"])
    positive = cases["mml-0959"]
    check("mml0959_required_chunk_survives_through_context", positive["merge"]["target_retained"] and positive["top_k_context"]["target_id_in_context"])
    check("rejected_candidate_raw_scores_are_explicitly_unknown", all(case["rerank"]["target_raw_score"] == "UNKNOWN_NOT_SAVED" for case in cases.values()))

    output = {
        "schema": "p33-frozen-record-consistency-check/v1",
        "status": "PASS",
        "command": "& 'E:\\RAG quention\\.venv\\Scripts\\python.exe' -B docs/evaluation/evidence/p33-evidence-loss-20261017/check_frozen_records.py",
        "exit_code": 0,
        "provider_calls": 0,
        "database_writes": 0,
        "checks": checks,
        "prior_attempt": {
            "exit_code": 1,
            "reason": "The first assertion used a nonexistent top-level source_version key; the read-only evidence stores it under chunk.metadata. No application code or data changed.",
        },
    }
    (EVIDENCE / "frozen-record-consistency-check.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: {len(checks)} frozen-evidence checks; 0 provider calls; 0 database writes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

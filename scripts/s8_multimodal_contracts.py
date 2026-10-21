"""S8.0 read-only frozen MML/Hotpot/Omni contracts; no OCR, DB or Provider calls."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import zipfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval_center.special_public_data import canonical
from scripts.m4_result_contract import audit_bindings, row_status, source_coverage


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def lines(path: Path):
    return [json.loads(s) for s in path.read_text(encoding="utf-8-sig").splitlines() if s.strip()]


def file_record(path: Path, expected: str | None = None) -> dict:
    result = {"path": str(path), "exists": path.is_file(), "expected_sha256": expected}
    if path.is_file():
        result.update(bytes=path.stat().st_size, sha256=sha(path.read_bytes()))
        result["sha256_matches"] = result["sha256"] == expected if expected else None
    else:
        result.update(sha256=None, sha256_matches=False if expected else None)
    return result


def validate_denominators(qa: list, pages: list) -> None:
    if len(qa) != 40 or len({x["case_id"] for x in qa}) != 40:
        raise ValueError("FROZEN_QA40_DENOMINATOR_INVALID")
    if Counter(x["dataset"] for x in qa) != Counter({
        "scifact": 8, "miracl-zh": 8, "longbench-zh": 8,
        "hotpotqa": 8, "mmlongbench-doc": 8,
    }):
        raise ValueError("FROZEN_QA_DATASET_DENOMINATOR_INVALID")
    if len(pages) != 24 or len({x["page_id"] for x in pages}) != 24:
        raise ValueError("FROZEN_OMNI24_DENOMINATOR_INVALID")


def current_binding(expected: dict, current: dict | None, chunks: list,
                    vectors: list | None = None, versions: dict | None = None) -> dict:
    """Identity only. A document hit is not a fact or embedding proof."""
    checks = {
        "present": current is not None,
        "tenant": bool(current and current.get("tenant_id") == 332),
        "kb": bool(current and current.get("knowledge_base_id") == expected.get("knowledge_base_id")),
        "version": bool(current and current.get("source_version") == expected.get("source_version")),
        "file_sha": bool(current and current.get("file_hash") == expected.get("file_hash")),
        "enabled_completed": bool(current and current.get("enable_status") == "enabled"
                                  and current.get("parse_status") == "completed"),
    }
    rows = [x for x in chunks if x.get("knowledge_id") == expected["id"]]
    raw = [x for x in rows if x.get("chunk_type") not in ("summary", "parent", "image_caption")]
    vector_rows = [x for x in (vectors or []) if x.get("knowledge_id") == expected["id"]]
    version_rows = [versions[x["id"]] for x in rows if versions and x["id"] in versions]
    return {"checks": checks, "identity_status": "CURRENT_EXACT" if all(checks.values()) else "MISMATCH_OR_MISSING",
            "current_document": current, "chunk_count": len(rows), "raw_chunk_count": len(raw),
            "enabled_ready_raw_chunks": sum(x.get("is_enabled") is True and x.get("index_status") == "ready" for x in raw),
            "vector_groups": vector_rows,
            "per_document_vector_status": "NONNULL_ENABLED_VECTOR_RECORDS_PRESENT" if any(
                x["dimension"] > 0 and x["nonnull"] > 0 and x["enabled"] > 0 for x in vector_rows
            ) else ("NO_POSITIVE_DIMENSION_VECTOR_RECORDS" if vectors is not None else "UNVERIFIED_KB_AGGREGATE_ONLY"),
            "chunk_version_rows": len(version_rows),
            "chunk_version_mismatches": [x["id"] for x in version_rows if x.get("chunk_source_version") is not None
                                          and x["chunk_source_version"] != expected.get("source_version")],
            "chunk_version_missing": [{"id": x["id"], "chunk_type": x["chunk_type"], "origin": x.get("origin")}
                                      for x in version_rows if x.get("chunk_source_version") is None],
            "fact_coverage": "NOT_ESTABLISHED_BY_IDENTITY"}


def locator_audit(pred: dict, image_sha: str) -> dict:
    text = pred.get("raw_ocr_text", "")
    checks, positions, invalid, no_bbox = [], [], [], []
    for i, loc in enumerate(pred.get("source_locators", [])):
        start, end = loc.get("start"), loc.get("end")
        raw = loc.get("raw_evidence") or {}
        bbox = loc.get("bbox")
        valid = (type(start) is int and type(end) is int and 0 <= start < end <= len(text)
                 and text[start:end] == loc.get("quote")
                 and raw.get("source_sha256") == image_sha
                 and raw.get("offset_space") == "derived_unicode_codepoints")
        positioned = (isinstance(bbox, list) and len(bbox) == 4
                      and all(type(v) in (int, float) and 0 <= v <= 1 for v in bbox))
        checks.append(valid)
        positions.append(positioned)
        if not valid:
            invalid.append(i)
        if not positioned:
            no_bbox.append(i)
    return {"locator_count": len(checks), "valid_count": sum(checks), "invalid_indices": invalid,
            "all_locators_valid": bool(checks) and all(checks),
            "bbox_positioned_count": sum(positions), "bbox_missing_or_invalid_indices": no_bbox,
            "text_sha256_matches": sha(text.encode()) == pred.get("derived_text_sha256"),
            "scope": "Exact derived-text offsets/source hash audited separately from optional normalized boxes; no semantic OCR accuracy or original-text-offset claim"}


def historical_record(row: dict | None) -> dict:
    if row is None:
        return {"status": "MISSING_RECEIPT", "new_validation": False}
    capture = row.get("stage_capture") or {}
    states = {s["stage"]: s["state"] for s in capture.get("stages", [])
              if s.get("status") == "completed" and isinstance(s.get("state"), dict)}
    coverage = {}
    required = [s for s, grade in row.get("qrels", {}).items() if grade > 0]
    mapping = row.get("document_to_source", {})
    for stage, key in (("retrieval", "search_results"), ("rerank", "rerank_results"), ("merge", "merge_results")):
        found = states.get(stage, {}).get(key, [])
        coverage[stage] = {"observed": stage in states, "candidate_count": len(found), **source_coverage(
            required, [mapping.get(x.get("knowledge_id") or x.get("document_id")) for x in found])}
    models = [{"request_id": c.get("request_id"), "provider": c.get("provider"), "model": c.get("model"),
               "model_id": c.get("model_id"), "capability": c.get("capability"), "outcome": c.get("outcome"),
               "wire_sha256": c.get("wire_sha256"), "usage_status": (c.get("usage") or {}).get("status"),
               "reported_total_tokens": (c.get("usage") or {}).get("total_tokens")}
              for c in row.get("provider_calls", [])]
    return {"status": row_status(row), "raw_tool_status": row.get("status"),
            "session_id": row.get("session_id"), "request_id": row.get("request_id"),
            "stage_capture_path": row.get("stage_capture_path"), "provider_calls": models,
            "historical_source_coverage": coverage, "new_validation": False,
            "source_commit": "9350f2282659571d14931e420d79e896901033f3",
            "fact_coverage": "SOURCE_METRICS_NOT_FACT_PROOF",
            "historical_unknown_action": "PRESERVE_NO_RESEND" if "UNKNOWN" in row_status(row) else None}


def archive_contract(dataset: dict) -> dict:
    objects = dataset["source_objects"]
    first = objects[0]
    path = Path(first["source_archive"])
    record = file_record(path, first["archive_sha256"])
    if not path.is_file():
        return {**record, "source_objects": [], "status": "MISSING_ARCHIVE"}
    checked = []
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for obj in objects:
            try:
                data = archive.read(obj["archive_object_path"])
                checked.append({"source_id": obj["source_id"], "member": obj["archive_object_path"],
                                "sha256": sha(data), "expected_sha256": obj["sha256"],
                                "bytes": len(data), "verified": sha(data) == obj["sha256"] and len(data) == obj["bytes"]})
            except KeyError:
                checked.append({"source_id": obj["source_id"], "verified": False, "reason": "MEMBER_MISSING"})
    return {**record, "manifest_schema": manifest["schema"], "prepared_source_units": len(manifest["units"]),
            "contains_gold": manifest.get("Gold_qrels_answers_included"), "source_objects": checked,
            "status": "VERIFIED" if record["sha256_matches"] and all(x["verified"] for x in checked) else "MISMATCH"}


def graph_contract(batches: list, current_docs: dict, current_chunks: dict) -> dict:
    contributions = []
    for batch in batches:
        source = batch["source"]
        doc, chunk = current_docs.get(source["document_id"]), current_chunks.get(source["chunk_id"])
        source_valid = bool(doc and chunk and source["tenant_id"] == 332
                            and doc["tenant_id"] == 332 and doc["knowledge_base_id"] == source["knowledge_base_id"]
                            and doc["source_version"] == source["source_version"]
                            and doc["enable_status"] == "enabled" and doc["parse_status"] == "completed"
                            and chunk["knowledge_id"] == source["document_id"]
                            and chunk["content"] == source["content"] and chunk["is_enabled"] is True)
        quote_valid = all(source["content"][e["quote_start"]:e["quote_end"]] == e["quote"] for e in batch["edges"])
        contributions.append({"document_id": source["document_id"], "source_version": source["source_version"],
                              "chunk_id": source["chunk_id"], "source_chars": len(source["content"]),
                              "source_sha256": sha(source["content"].encode()), "current_source_matches": source_valid,
                              "exact_quote_offsets_valid": quote_valid, "nodes": len(batch["nodes"]), "edges": len(batch["edges"]),
                              "rejected": len(batch["rejected"]), "extraction_id": batch["extraction_id"]})
    docs = sorted({x["document_id"] for x in contributions})
    return {"kind": "REUSED_REAL_S7_RESPONSES_NOT_NEW_SEND", "tenant_id": 332,
            "contribution_count": len(contributions), "document_count": len(docs), "document_ids": docs,
            "nodes": sum(x["nodes"] for x in contributions), "edges": sum(x["edges"] for x in contributions),
            "contributions": contributions, "current_pg_graph_record_status": "UNVERIFIED_SAVED_RESPONSE_ONLY",
            "fact_and_bridge_coverage": "NOT_PROVEN_BY_SOURCE_COVERAGE",
            "limits": {"source_chunks_per_document": 12, "source_characters_per_document": 32768,
                       "max_paths": 3, "max_hops": 2, "max_outgoing": 12, "graph_raw_chunks": 4,
                       "graph_raw_chars": 4096, "total_context_chars": 14000},
            "known_limits": ["Exact anchor aliases do not bridge Franklin's Leyden jar to A Leyden jar/Leiden jar",
                             "Frost seminar/location relation was not accepted; source coverage is not a recovered geographic fact",
                             "Romeo source coverage improved but final director remained wrong",
                             "Books off/on had identical generation wire; answer difference cannot be attributed to Graph",
                             "Tribometer received irrelevant jar-capacitance-unit evidence"]}


def build(m4: Path, p9: Path, s7: Path, runtime_path: Path, inputs: Path,
          addendum_path: Path) -> dict:
    qa, pages = load(m4 / "qa_cases.json"), load(m4 / "omni_pages.json")
    validate_denominators(qa, pages)
    frozen, gold, runtime = load(p9 / "frozen-inventory.json"), load(p9 / "gold-all.json"), load(runtime_path)
    original_cases = {(d["name"], c["qid"]): c for d in gold["datasets"] for c in d["cases"]}
    gold_mismatches = [c["case_id"] for c in qa if any(
        c[k] != original_cases[(c["dataset"], c["qid"])][k] for k in ("question", "qrels", "answers"))]
    if gold_mismatches:
        raise ValueError("FROZEN_GOLD_INPUT_MISMATCH:" + ",".join(gold_mismatches))
    identity = next(x for x in runtime["identities"] if x["tenant_id"] == 332)
    addendum = next(x for x in load(addendum_path)["identities"] if x["tenant_id"] == 332)
    vectors = addendum["document_vectors"]
    versions = {x["id"]: x for x in addendum["chunk_version_status"]}
    image_metadata = addendum["image_metadata"]
    docs = {x["id"]: x for x in identity["documents"]}
    chunks = identity["chunks"]
    historical = {x["case_id"]: x for x in lines(m4 / "api-execution/qa_results.jsonl")}
    preds = {x["page_id"]: x for x in lines(m4 / "mineru/pages.jsonl")}
    scores = {x["page_id"]: x for x in load(m4 / "omni_scored.json")["pages"]}
    if set(preds) != {x["page_id"] for x in pages} or set(scores) != set(preds):
        raise ValueError("OMNI_RESULT_OR_SCORE_DENOMINATOR_MISMATCH")
    contracts = []
    for case in qa:
        if case["dataset"] not in ("mmlongbench-doc", "hotpotqa"):
            continue
        contracts.append({"case_id": case["case_id"], "qid": case["qid"], "dataset": case["dataset"],
                          "dataset_version": case["dataset_version"], "question": case["question"],
                          "kb_id": case["knowledge_base_id"], "document_scope": case["document_scope"],
                          "gold_scoring_only": {"answers": case["answers"], "qrels": case["qrels"],
                                                "qrels_kind": case["qrels_kind"], **case["evaluation_metadata"]},
                          "binding_audit": audit_bindings(case),
                          "sources": [{"frozen": d, "current": current_binding(d, docs.get(d["id"]), chunks, vectors, versions)}
                                      for d in case["expected_documents"]],
                          "historical_full_rag": historical_record(historical.get(case["case_id"])),
                          "new_s8_execution": "NOT_RUN_BLOCKED_AUTHORIZATION"})
    page_rows = []
    from PIL import Image
    for page in pages:
        pred, score = preds[page["page_id"]], scores[page["page_id"]]
        image = file_record(Path(page["image_path"]), page["image_sha256"])
        if image["exists"]:
            with Image.open(page["image_path"]) as bitmap:
                image.update(width=bitmap.width, height=bitmap.height, format=bitmap.format)
        expected = {"id": page["document_id"], "source_version": page["source_version"],
                    "knowledge_base_id": page["knowledge_base_id"], "file_hash": page["image_sha256"]}
        binding = current_binding(expected, docs.get(page["document_id"]), chunks, vectors, versions)
        saved_image_info = [x for x in image_metadata if x["knowledge_id"] == page["document_id"]]
        gold_valid = sha(canonical(page["gold"]).encode()) == page["page_gold_sha256"]
        page_rows.append({"page_id": page["page_id"], "filename": page["filename"], "image": image,
                          "gold_sha256": page["page_gold_sha256"], "gold_sha_verified": gold_valid,
                          "gold_dimensions": {k: page["gold"].get("page_info", {}).get(k) for k in ("width", "height")},
                          "gold_categories": dict(Counter(x.get("category_type", "UNKNOWN") for x in page["gold"].get("layout_dets", []))),
                          "gold_ignored_regions": sum(bool(x.get("ignore")) for x in page["gold"].get("layout_dets", [])),
                          "existing_index": binding, "existing_index_ocr_producer": "UNVERIFIED_IMAGE_ENGINE_ALONE_IS_INSUFFICIENT",
                          "existing_image_metadata": saved_image_info,
                          "saved_mineru_parse": {k: pred.get(k) for k in ("status", "parser_engine", "parser_version", "derived_text_sha256",
                                                                         "content_chars", "elapsed_seconds", "provider_calls", "database_writes")},
                          "layout_mapping": locator_audit(pred, page["image_sha256"]),
                          "asset_manifest_entries": len(pred.get("assets", [])),
                          "sidecar_content_availability": "UNVERIFIED_LOGICAL_MANIFEST_ONLY",
                          "scoring_mask": score["metric"], "official_qa_gold": False,
                          "s8_parse_action": "REUSE_SAVED_MINERU_PARSE_NO_REPARSE",
                          "s8_rag_action": "NOT_RUN_CURRENT_MINERU_PERSISTENCE_NOT_PROVEN",
                          "scope": "Official parsing annotations, not official QA; 21 unmatched pages remain in denominator"})
    archive_rows = {d["name"]: archive_contract(d) for d in frozen["datasets"]
                    if d["name"] in ("hotpotqa", "mmlongbench-doc")}
    omni_archive_path = Path("D:/RAG-v6-final-ingestion-pack-r1/attempt3-ocr-layout/omnidocbench.zip")
    omni_archive = file_record(omni_archive_path, frozen["omnidocbench"]["input_archive_sha256"])
    if omni_archive_path.is_file():
        with zipfile.ZipFile(omni_archive_path) as archive:
            units = json.loads(archive.read("manifest.json"))["units"]
            omni_archive["prepared_source_units"] = len(units)
            omni_archive["frozen_image_members_verified"] = sum(
                sha(archive.read("objects/" + x["image_sha256"])) == x["image_sha256"] for x in pages)
            by_image = {u["source"]["artifact"]["sha256"]: u for u in units}
            for page in page_rows:
                unit = by_image[page["image"]["expected_sha256"]]
                normalized_meta = unit["prepared"]["normalized_json"]
                normalized_bytes = archive.read(normalized_meta["path"])
                normalized = json.loads(normalized_bytes)
                prepared_ocr_sha = sha(normalized["markdown_content"].encode())
                current_ocr_shas = [i.get("ocr_text_sha256") for r in page["existing_image_metadata"]
                                    for i in r["image_info"].get("items", [])]
                page["historical_prepared_parser"] = {
                    "engine": normalized["parser_engine"], "version": normalized["parser_version"],
                    "normalized_member": normalized_meta["path"],
                    "normalized_sha_verified": sha(normalized_bytes) == normalized_meta["sha256"],
                    "ocr_text_sha256": prepared_ocr_sha,
                    "current_ocr_matches_prepared": prepared_ocr_sha in current_ocr_shas}
                if prepared_ocr_sha in current_ocr_shas and "tesseract" in normalized["parser_engine"].lower():
                    page["existing_index_ocr_producer"] = "HISTORICAL_TESSERACT_EXACT_TEXT_SHA_MATCH"
                elif not current_ocr_shas:
                    doc_id = (page["existing_index"]["current_document"] or {}).get("id")
                    existing_rows = [r for r in chunks if r["knowledge_id"] == doc_id]
                    if existing_rows and all(r["content"].strip().startswith("![")
                                             and r["content"].strip().endswith(")") for r in existing_rows):
                        page["existing_index_ocr_producer"] = "NO_OCR_IMAGE_MARKDOWN_PLACEHOLDER_ONLY"
    graph = graph_contract(load(s7 / "graph-build/graph-batches.json")["batches"], docs, {x["id"]: x for x in chunks})
    current_graph = addendum["graph_current_validated"]
    graph["current_pg_graph_record_status"] = current_graph["status"]
    graph["current_pg_counts"] = {k: current_graph[k] for k in ("contributions", "nodes", "edges")}
    graph["current_saved_contribution_identity_equal"] = sorted(
        (x["source"]["chunk_id"], x["source"]["source_version"]) for x in current_graph["batches"]
    ) == sorted((x["chunk_id"], x["source_version"]) for x in graph["contributions"])
    remaining = []
    for doc in docs.values():
        if doc["knowledge_base_id"] != "96a9eebd-ffb2-4bc6-9bea-3ed015845479" or doc["id"] in graph["document_ids"]:
            continue
        raw = [r for r in chunks if r["knowledge_id"] == doc["id"] and r["chunk_type"] in ("text", "image_ocr")
               and r["is_enabled"] is True and r["index_status"] == "ready"]
        remaining.append({"document_id": doc["id"], "source_version": doc["source_version"],
                          "file_sha256": doc["file_hash"], "raw_chunks": len(raw),
                          "source_characters": sum(len(r["content"]) for r in raw),
                          "within_existing_per_document_limits": bool(raw) and len(raw) <= 12
                          and sum(len(r["content"]) for r in raw) <= 32768,
                          "action": "NOT_RUN_NEW_GRAPH_EXTRACTION_REQUIRES_AUTHORIZATION"})
    graph["remaining_current24_pool_possible_build_scope"] = {
        "documents": remaining, "document_count": len(remaining),
        "minimum_one_request_per_raw_chunk": sum(x["raw_chunks"] for x in remaining),
        "total_source_characters": sum(x["source_characters"] for x in remaining),
        "status": "PLAN_ONLY_NOT_AUTHORIZATION_NOT_ACTUAL_BUILD",
        "excludes": "49 missing original background documents; never invent their graph or substitute tenant1 graph"}
    for case in contracts:
        if case["dataset"] == "hotpotqa":
            required = [s["frozen"]["id"] for s in case["sources"]]
            case["graph_source_readiness"] = {**source_coverage(required, graph["document_ids"]),
                                               "meaning": "Saved graph source-doc contributions, not required relation/fact coverage"}
    mml_sources = load(inputs / "mmlongbench-doc-sources.json")
    for source in mml_sources:
        source["current_matching_file_sha_documents"] = [
            {"id": d["id"], "knowledge_base_id": d["knowledge_base_id"], "source_version": d["source_version"],
             "parser_engine": d["parser_engine"], "parse_status": d["parse_status"], "enable_status": d["enable_status"]}
            for d in docs.values() if d["file_hash"] == source["sha256"]]
    hotpot_units = []
    hotpot_archive = Path(archive_rows["hotpotqa"]["path"])
    with zipfile.ZipFile(hotpot_archive) as archive:
        for unit in json.loads(archive.read("manifest.json"))["units"]:
            source = unit["source"]
            artifact = source["artifact"]
            source_bytes = archive.read(artifact["path"])
            matches = [d for d in docs.values() if d["knowledge_base_id"] == "96a9eebd-ffb2-4bc6-9bea-3ed015845479"
                       and d["file_hash"] == artifact["sha256"] and d["file_name"] == source["file_name"]]
            hotpot_units.append({"source_id": source["source_document_id"], "title": source["provenance"]["original_title"],
                                 "file_name": source["file_name"], "member": artifact["path"],
                                 "source_sha256": artifact["sha256"], "source_bytes": len(source_bytes),
                                 "source_member_verified": sha(source_bytes) == artifact["sha256"] and len(source_bytes) == artifact["bytes"],
                                 "current_matching_documents": matches,
                                 "current_index_status": "INDEXED" if matches else "NOT_INDEXED",
                                 "runtime_selection": "NONE_INVENTORY_ONLY_NOT_GOLD_ROUTING"})
    prepared = {}
    for name in ("mmlongbench-doc", "hotpotqa"):
        prepared_cases = lines(inputs / (name + "-cases.jsonl"))
        frozen_cases = [c for c in qa if c["dataset"] == name]
        prepared[name] = {"path": str(inputs / (name + "-cases.jsonl")), "case_count": len(prepared_cases),
                          "qids": [c["qid"] for c in prepared_cases],
                          "exact_frozen8_qid_set": {c["qid"] for c in prepared_cases} == {c["qid"] for c in frozen_cases},
                          "additional_prepared_qids": [c["qid"] for c in prepared_cases if c["qid"] not in {a["qid"] for a in frozen_cases}],
                          "scope": "The local frozen special adapter supplies exactly8 selected QA, not full public benchmark; no independent prepared holdout is registered here"}
    summary = {"frozen_qa_total": 40, "owned_qa_total": len(contracts), "mml_qids": 8, "hotpot_qids": 8,
               "omni_pages_total": 24, "omni_images_sha_valid": sum(p["image"].get("sha256_matches") is True for p in page_rows),
               "omni_gold_sha_valid": sum(p["gold_sha_verified"] for p in page_rows),
               "mineru_saved_parse_complete": sum(p["saved_mineru_parse"]["status"] == "COMPLETE" for p in page_rows),
               "mineru_saved_database_writes": sum(p["saved_mineru_parse"]["database_writes"] or 0 for p in page_rows),
               "omni_project_scored_pages": sum(p["scoring_mask"]["status"] == "SCORED_PROJECT_PAGE_TEXT_NED" for p in page_rows),
               "omni_not_scorable_pages": sum(p["scoring_mask"]["status"] == "NOT_SCORABLE" for p in page_rows),
               "omni_current_tesseract_exact_sha": sum(p["existing_index_ocr_producer"] == "HISTORICAL_TESSERACT_EXACT_TEXT_SHA_MATCH" for p in page_rows),
               "omni_current_placeholder_only": sum(p["existing_index_ocr_producer"] == "NO_OCR_IMAGE_MARKDOWN_PLACEHOLDER_ONLY" for p in page_rows),
               "hotpot_prepared_full_context_sources": len(hotpot_units),
               "hotpot_current_indexed_full_context_sources": sum(bool(p["current_matching_documents"]) for p in hotpot_units),
               "current_exact_source_bindings": sum(s["current"]["identity_status"] == "CURRENT_EXACT" for c in contracts for s in c["sources"]),
               "new_provider_calls": 0, "new_database_writes": 0, "new_parses": 0,
               "stage_status": "PARTIAL_CURRENT_RAG_INDEX_AND_AUTHORIZATION_GAPS"}
    return {"schema": "s8-multimodal-source-contracts/v1", "kind": "ZERO_CLOUD_READ_ONLY_PREPARATION",
            "source_baseline": "b9268e2d6707f73c48b5624a96e39879d02ce12c", "summary": summary,
            "denominators": {"all_frozen_qids": [{"case_id": c["case_id"], "qid": c["qid"], "dataset": c["dataset"]} for c in qa],
                             "all40_question_qrels_answers_equal_original_gold": not gold_mismatches,
                             "public_full_benchmarks": "NOT_DOWNLOADED_NOT_EQUIVALENT_TO_PROJECT_SUBSETS",
                             "prepared_hotpot_sources": 73, "currently_indexed_hotpot_sources": sum(d["knowledge_base_id"] == "96a9eebd-ffb2-4bc6-9bea-3ed015845479" for d in docs.values()),
                             "mml_source_pdfs": 3, "omni_parse_pages": 24},
            "input_files": [file_record(p) for p in [m4 / "qa_cases.json", m4 / "omni_pages.json", p9 / "gold-all.json",
                                                      p9 / "frozen-inventory.json", runtime_path, addendum_path, s7 / "graph-build/graph-batches.json",
                                                      inputs / "mmlongbench-doc-cases.jsonl", inputs / "hotpotqa-cases.jsonl"]],
            "archives": {**archive_rows, "omnidocbench": omni_archive},
            "prepared_qa_scope": prepared,
            "hotpot_full_prepared_sources": hotpot_units,
            "mml_original_pdfs": [{**s, "actual_file": file_record(Path(s["path"]), s["sha256"])} for s in mml_sources],
            "cases": contracts, "omni_pages": page_rows, "graph": graph,
            "metric_policy": {"mml": "Official native typed answers; separate source/page/fact support. No parsing Gold. EM/F1 project normalization only, not official GPT judged score.",
                              "hotpot": "Official answer EM/token-F1 plus separate any-source/all-required-source and exact supporting-sentence fact coverage; joint score only with validated support mapping.",
                              "omni": "24 official parsing annotations; reuse 3 validated project literal page NED masks and retain 21 NOT_SCORABLE. No official QA accuracy; official layout/table/formula mapping not implemented."},
            "readiness": {"A": "QA_SOURCE_IDENTITIES_READY_PENDING_RUNTIME_MODELS_AND_NEW_AUTHORIZATION; Omni24 parsing reuse only, not24RAG QA",
                          "C": "CURRENT_GRAPH_LOADED12docs19contributions; not full73doc corpus graph and no guaranteed all-hop facts; controlled subset only",
                          "new_auth_required": True, "historical_unknown": "PRESERVED_NO_REPLAY_NO_RELEASE"}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m4", type=Path, default=Path("E:/WK/run/m4-full-sweep-20261018"))
    parser.add_argument("--p9", type=Path, default=Path("E:/WK/run/p9-20261016"))
    parser.add_argument("--s7", type=Path, default=Path("E:/WK/run/s7-20261019"))
    parser.add_argument("--runtime", type=Path, default=Path("E:/WK/run/s8-20261019/runtime-inventory.json"))
    parser.add_argument("--addendum", type=Path, default=Path("E:/WK/run/s8-20261019/runtime-inventory.addendum.json"))
    parser.add_argument("--inputs", type=Path, default=Path("E:/RAG quention/var/reports/v6-cohort-report-specials-r1/attempt1/verification-1/inputs"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.m4, args.p9, args.s7, args.runtime, args.inputs, args.addendum)
    if args.output.resolve() in {Path(x["path"]).resolve() for x in result["input_files"]}:
        parser.error("OUTPUT_MUST_NOT_OVERWRITE_FROZEN_INPUT")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), **result["summary"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

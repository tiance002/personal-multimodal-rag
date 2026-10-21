"""Synthetic contracts only: inventory cannot turn identity or an absent run into fact proof."""
import pytest

from scripts.s8_multimodal_contracts import (
    current_binding, graph_contract, historical_record, locator_audit, sha, validate_denominators,
)


def test_frozen_denominators_require_all40_and_all24_unique():
    names = ("scifact", "miracl-zh", "longbench-zh", "hotpotqa", "mmlongbench-doc")
    cases = [{"case_id": f"{n}-{i}", "dataset": n} for n in names for i in range(8)]
    pages = [{"page_id": str(i)} for i in range(24)]
    validate_denominators(cases, pages)
    with pytest.raises(ValueError, match="QA40"):
        validate_denominators(cases[:-1], pages)
    with pytest.raises(ValueError, match="OMNI24"):
        validate_denominators(cases, pages[:-1] + [pages[0]])


def test_binding_keeps_source_identity_separate_from_fact_and_vectors():
    expected = {"id": "doc", "knowledge_base_id": "kb", "source_version": "v1", "file_hash": "sha"}
    current = {**expected, "tenant_id": 332, "enable_status": "enabled", "parse_status": "completed"}
    result = current_binding(expected, current, [])
    assert result["identity_status"] == "CURRENT_EXACT"
    assert result["fact_coverage"] == "NOT_ESTABLISHED_BY_IDENTITY"
    assert result["per_document_vector_status"] == "UNVERIFIED_KB_AGGREGATE_ONLY"
    result = current_binding(expected, {**current, "source_version": "v2"}, [])
    assert result["identity_status"] == "MISMATCH_OR_MISSING"
    result = current_binding(expected, current, [{"id": "s", "knowledge_id": "doc", "chunk_type": "summary"}],
                             versions={"s": {"id": "s", "chunk_type": "summary", "chunk_source_version": None}})
    assert result["chunk_version_mismatches"] == []
    assert result["chunk_version_missing"][0]["id"] == "s"


def test_keyword_dimension_zero_does_not_establish_vector_index():
    expected = {"id": "doc"}
    result = current_binding(expected, None, [], [{"knowledge_id": "doc", "dimension": 0, "nonnull": 0, "enabled": 1}])
    assert result["per_document_vector_status"] == "NO_POSITIVE_DIMENSION_VECTOR_RECORDS"


def test_mineru_exact_text_mapping_does_not_require_optional_bbox():
    pred = {"raw_ocr_text": "A matrix", "derived_text_sha256": sha(b"A matrix"),
            "source_locators": [{"start": 0, "end": 8, "quote": "A matrix", "bbox": None,
                                 "raw_evidence": {"source_sha256": "image", "offset_space": "derived_unicode_codepoints"}}]}
    result = locator_audit(pred, "image")
    assert result["all_locators_valid"]
    assert result["bbox_positioned_count"] == 0
    pred["source_locators"][0]["quote"] = "Gold matrix"
    assert locator_audit(pred, "image")["invalid_indices"] == [0]


def test_empty_locator_or_missing_receipt_is_not_validation_pass():
    assert locator_audit({"raw_ocr_text": ""}, "image")["all_locators_valid"] is False
    assert historical_record(None)["status"] == "MISSING_RECEIPT"


def test_unknown_is_retained_and_not_zero_usage_or_new_validation():
    row = {"status": "FAILED", "unknown_call_count": 1,
           "provider_calls": [{"outcome": "UNKNOWN", "usage": {"status": "UNREPORTED"}}]}
    result = historical_record(row)
    assert result["status"] == "UNKNOWN"
    assert result["historical_unknown_action"] == "PRESERVE_NO_RESEND"
    assert result["provider_calls"][0]["reported_total_tokens"] is None
    assert not result["new_validation"]


def test_saved_graph_requires_current_version_content_and_exact_quote():
    source = {"tenant_id": 332, "document_id": "d", "knowledge_base_id": "k", "source_version": "v1",
              "chunk_id": "c", "content": "Alice founded Alpha"}
    batch = {"source": source, "nodes": [], "edges": [{"quote_start": 0, "quote_end": 19, "quote": "Alice founded Alpha"}],
             "rejected": [], "extraction_id": "saved-only"}
    docs = {"d": {"tenant_id": 332, "knowledge_base_id": "k", "source_version": "v1",
                  "enable_status": "enabled", "parse_status": "completed"}}
    chunks = {"c": {"knowledge_id": "d", "content": source["content"], "is_enabled": True}}
    result = graph_contract([batch], docs, chunks)
    assert result["contributions"][0]["current_source_matches"]
    assert result["contributions"][0]["exact_quote_offsets_valid"]
    assert result["fact_and_bridge_coverage"] == "NOT_PROVEN_BY_SOURCE_COVERAGE"
    docs["d"]["source_version"] = "v2"
    assert not graph_contract([batch], docs, chunks)["contributions"][0]["current_source_matches"]

import json

from scripts.validate_eval import validate


def test_quality_v1_refusal_keeps_required_fields_but_allows_empty_expectations(tmp_path):
    path = tmp_path / "quality.jsonl"
    path.write_text(json.dumps({
        "question": "资料里没有的成本是多少？", "expected_chunk_ids": [],
        "answer_points": [], "kb_scope": ["kb"], "dataset_version": "quality-v1",
        "scenario": "no_evidence", "expected_outcome": "refuse", "expected_target_count": 1,
    }, ensure_ascii=False), encoding="utf-8")

    assert validate(path, schema="quality-v1")["status"] == "PASS"
    assert validate(path, schema="core-v1")["status"] == "FAIL"


def test_quality_v1_full_answer_still_requires_expected_evidence(tmp_path):
    path = tmp_path / "quality.jsonl"
    path.write_text(json.dumps({
        "question": "A成本是多少？", "expected_chunk_ids": [], "answer_points": [],
        "kb_scope": ["kb"], "dataset_version": "quality-v1",
        "scenario": "simple_fact", "expected_outcome": "full", "expected_target_count": 1,
    }, ensure_ascii=False), encoding="utf-8")

    assert validate(path, schema="quality-v1")["status"] == "FAIL"


def test_quality_v1_optional_case_id_is_checked_for_shape_and_uniqueness(tmp_path):
    path = tmp_path / "quality.jsonl"
    rows = [
        {
            "question": "A成本是多少？", "expected_chunk_ids": ["cost-a"],
            "answer_points": ["A方案采购成本1000元"], "kb_scope": ["costs"],
            "dataset_version": "quality-v1", "scenario": "simple_fact",
            "expected_outcome": "full", "expected_target_count": 1, "case_id": "quality-001",
        },
        {
            "question": "B成本是多少？", "expected_chunk_ids": ["cost-b"],
            "answer_points": ["B方案采购成本1200元"], "kb_scope": ["costs"],
            "dataset_version": "quality-v1", "scenario": "simple_fact",
            "expected_outcome": "full", "expected_target_count": 1, "case_id": "quality-001",
        },
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

    assert validate(path, schema="quality-v1")["status"] == "FAIL"

    rows[1]["case_id"] = "quality-002"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    assert validate(path, schema="quality-v1")["status"] == "PASS"

    rows[1]["case_id"] = "case_0123456789AB"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    assert validate(path, schema="quality-v1")["status"] == "FAIL"


def test_case_id_is_additive_and_does_not_replace_frozen_required_fields(tmp_path):
    path = tmp_path / "core.jsonl"
    path.write_text(json.dumps({
        "question": "问题", "expected_chunk_ids": ["chunk"], "answer_points": ["要点"],
        "kb_scope": ["kb"], "case_id": "core-001",
    }, ensure_ascii=False), encoding="utf-8")

    result = validate(path, schema="core-v1")
    assert result["status"] == "PASS"
    assert result["required_fields"] == ["answer_points", "expected_chunk_ids", "kb_scope", "question"]

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

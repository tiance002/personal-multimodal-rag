import hashlib
import json

import pytest

from eval_center.public_data import (
    DATASET_VERSIONS,
    SCHEMA_VERSION,
    fold_chunk_ranking,
    longbench_documents,
    load_public_dataset,
    parse_qrels_tsv,
    require_split_for_phase,
)


def test_chunk_rankings_fold_to_unique_source_documents_by_first_seen_rank():
    mapping = {
        "chunk-a1": "doc-a",
        "chunk-a2": "doc-a",
        "chunk-b1": "doc-b",
        "chunk-c1": "doc-c",
    }

    assert fold_chunk_ranking(
        ["chunk-a1", "chunk-a2", "chunk-b1", "chunk-c1", "chunk-b1"], mapping
    ) == ["doc-a", "doc-b", "doc-c"]


def test_unknown_chunk_mapping_invalidates_public_document_ranking():
    with pytest.raises(ValueError, match="unknown chunk"):
        fold_chunk_ranking(["chunk-missing"], {})


def test_qrels_keep_explicit_nonrelevant_judgments_and_original_ids():
    qrels = parse_qrels_tsv(
        "query-id\tcorpus-id\tscore\n"
        "1000222#0\t453#48\t1\n"
        "1000222#0\t95451#0\t0\n"
    )

    assert qrels == {"1000222#0": {"453#48": 1, "95451#0": 0}}


def test_miracl_four_column_qrels_keep_explicit_judgments_without_header():
    qrels = parse_qrels_tsv("1000222#0\tQ0\t453#48\t1\n1000222#0\tQ0\t95451#0\t0\n")

    assert qrels == {"1000222#0": {"453#48": 1, "95451#0": 0}}


def test_longbench_index_documents_contain_context_only():
    rows = longbench_documents(
        [
            {
                "_id": "sample-1",
                "dataset": "multifieldqa_zh",
                "context": "原文上下文",
                "input": "问题不能入库",
                "answers": ["参考答案不能入库"],
            }
        ]
    )

    assert rows == [{"doc_id": "multifieldqa_zh:sample-1", "title": "multifieldqa_zh sample-1", "text": "原文上下文"}]
    assert all("question" not in row and "answers" not in row and "qrels" not in row for row in rows)


def test_public_loader_verifies_hashes_and_preserves_split_metadata(tmp_path):
    dataset_dir = tmp_path / "prepared" / "adapters" / "scifact"
    dataset_dir.mkdir(parents=True)
    source = tmp_path / "source.bin"
    source.write_bytes(b"pinned source")
    files = {
        "corpus.jsonl": '{"doc_id":"d1","text":"source","title":"Title"}\n',
        "cases/development.jsonl": '{"qid":"q1","question":"question","qrels":{"d1":1},"split":"development"}\n',
        "cases/locked_holdout.jsonl": '{"qid":"q2","question":"locked question","qrels":{"d1":1},"split":"locked_holdout"}\n',
    }
    records = []
    for relative, content in files.items():
        path = dataset_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = content.encode()
        path.write_bytes(payload)
        records.append({"path": relative, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    source_payload = source.read_bytes()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset": "scifact",
        "dataset_version": DATASET_VERSIONS["scifact"],
        "files": records,
        "source_files": [{"path": str(source), "bytes": len(source_payload),
                          "sha256": hashlib.sha256(source_payload).hexdigest()}],
    }
    (dataset_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    development = load_public_dataset("scifact", tmp_path)
    assert development.cases[0].qrels == {"d1": 1}
    with pytest.raises(ValueError, match="locked split is validation-only"):
        load_public_dataset("scifact", tmp_path, split="locked_holdout", phase="baseline")
    locked = load_public_dataset("scifact", tmp_path, split="locked_holdout", phase="validate")
    assert locked.cases[0].split == "locked_holdout"

    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="public source cache hash mismatch"):
        load_public_dataset("scifact", tmp_path)


@pytest.mark.parametrize(
    ("split", "phase", "allowed"),
    [
        ("development", "baseline", "development"),
        ("development", "optimize", "development"),
        ("locked_holdout", "baseline", None),
        ("locked_holdout", "optimize", None),
        ("locked_holdout", "validate", "locked_holdout"),
    ],
)
def test_locked_split_is_available_only_to_final_validation(split, phase, allowed):
    if allowed is None:
        with pytest.raises(ValueError, match="locked split is validation-only"):
            require_split_for_phase(split, phase)
    else:
        assert require_split_for_phase(split, phase) == allowed

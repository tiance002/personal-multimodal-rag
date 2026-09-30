from __future__ import annotations

import hashlib
import json

from eval_center.development_data import load_development_dataset


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")
    return path.read_bytes()


def test_development_loader_reads_only_corpus_and_development_cases(tmp_path):
    dataset_dir = tmp_path / "prepared" / "adapters" / "scifact"
    corpus = _write(dataset_dir / "corpus.jsonl", '{"doc_id":"doc-1","title":"T","text":"Source"}\n')
    cases = _write(dataset_dir / "cases" / "development.jsonl",
                   '{"qid":"q-dev","question":"Question?","qrels":{"doc-1":1},"split":"development"}\n')
    manifest = {
        "schema_version": "public-rag-bench-v1",
        "dataset": "scifact",
        "dataset_version": "beir-scifact-5f7d1de60b170fc8027bb7898e2efca1",
        "files": [
            {"path": "corpus.jsonl", "bytes": len(corpus), "sha256": hashlib.sha256(corpus).hexdigest()},
            {"path": "cases/development.jsonl", "bytes": len(cases), "sha256": hashlib.sha256(cases).hexdigest()},
            # This record intentionally points to no file. The Development loader must ignore it.
            {"path": "cases/locked_holdout.jsonl", "bytes": 999, "sha256": "f" * 64},
        ],
        "source_files": [{"path": str(tmp_path / "locked-source-not-present.jsonl")}],
    }
    manifest_path = dataset_dir / "manifest.json"
    _write(manifest_path, json.dumps(manifest))

    dataset = load_development_dataset("scifact", tmp_path)

    assert dataset.split == "development"
    assert [case.qid for case in dataset.cases] == ["q-dev"]
    assert dataset.documents[0].doc_id == "doc-1"
    assert dataset.corpus_sha256 == hashlib.sha256(corpus).hexdigest()
    assert dataset.development_cases_sha256 == hashlib.sha256(cases).hexdigest()
    assert dataset.manifest_sha256 == hashlib.sha256(manifest_path.read_bytes()).hexdigest()

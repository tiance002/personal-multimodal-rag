"""Deterministic adapters for locally prepared public RAG benchmarks.

Raw public data lives outside the repository. Preparation writes a small,
hash-bound interchange format that keeps indexed source text separate from
questions, qrels, answers, and split metadata.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "public-rag-bench-v1"
LONG_BENCH_SEED = 20260928
DEFAULT_DATA_ROOT = Path(r"D:\RAG-Public-Bench")

DATASET_VERSIONS = {
    "scifact": "beir-scifact-5f7d1de60b170fc8027bb7898e2efca1",
    "miracl-zh": "MIRACL-ZH-CANDIDATE-POOL-SHARD0-SEED20260928-V1",
    "longbench-zh": "LongBench-ZH-HF-5e628be450b7e67fb7ae6e201bd6d8f7056f7672-SEED20260928-V1",
}
SOURCE_URLS = {
    "scifact": ("https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip",
                "https://github.com/allenai/scifact"),
    "miracl-zh": ("https://huggingface.co/datasets/miracl/miracl",
                  "https://huggingface.co/datasets/miracl/miracl-corpus"),
    "longbench-zh": ("https://huggingface.co/datasets/THUDM/LongBench",
                     "https://github.com/THUDM/LongBench"),
}

_FORBIDDEN_CORPUS_FIELDS = {
    "answer", "answers", "case_id", "context", "gold", "input", "qrels",
    "qid", "question", "reference", "references", "split",
}


@dataclass(frozen=True)
class PublicDocument:
    doc_id: str
    title: str
    text: str


@dataclass(frozen=True)
class PublicCase:
    qid: str
    question: str
    qrels: dict[str, int]
    split: str
    answers: tuple[str, ...] = ()
    paired_source_doc_id: str | None = None
    qrels_kind: str = "official_document_qrels"


@dataclass(frozen=True)
class PublicDataset:
    name: str
    dataset_version: str
    split: str
    manifest: dict[str, Any]
    documents: tuple[PublicDocument, ...]
    cases: tuple[PublicCase, ...]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path.name}:{number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"invalid JSONL object at {path.name}:{number}")
        rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(payload, encoding="utf-8", newline="\n")


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "bytes": len(payload), "sha256": _sha256(payload)}


def _source_file_record(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {"path": str(path), "bytes": len(payload), "sha256": _sha256(payload)}


def fold_chunk_ranking(chunk_ids: list[str], chunk_to_document: dict[str, str]) -> list[str]:
    """Fold real chunk ranks to distinct source docs at first occurrence."""
    if not isinstance(chunk_ids, list) or any(not isinstance(item, str) or not item for item in chunk_ids):
        raise ValueError("invalid chunk ranking")
    if not isinstance(chunk_to_document, dict):
        raise ValueError("invalid chunk-to-document mapping")
    seen: set[str] = set()
    documents: list[str] = []
    for chunk_id in chunk_ids:
        document_id = chunk_to_document.get(chunk_id)
        if not isinstance(document_id, str) or not document_id:
            raise ValueError(f"unknown chunk in ranking: {chunk_id}")
        if document_id not in seen:
            documents.append(document_id)
            seen.add(document_id)
    return documents


def parse_qrels_tsv(payload: str) -> dict[str, dict[str, int]]:
    """Parse BEIR/MIRACL qrels while retaining explicitly judged grade zero."""
    lines = [line.split("\t") for line in payload.splitlines() if line.strip()]
    if not lines:
        raise ValueError("empty qrels")
    header = [field.strip().lower() for field in lines[0]]
    aliases = {
        "query-id": "qid", "query_id": "qid", "qid": "qid",
        "corpus-id": "docid", "corpus_id": "docid", "docid": "docid", "document-id": "docid",
        "score": "grade", "relevance": "grade", "grade": "grade",
    }
    columns = [aliases.get(field) for field in header]
    if {"qid", "docid", "grade"} <= set(columns):
        qid_index, docid_index, grade_index = (columns.index(name) for name in ("qid", "docid", "grade"))
        data_rows, first_line = lines[1:], 2
    elif len(lines[0]) == 4 and lines[0][1].strip().upper() == "Q0":
        qid_index, docid_index, grade_index = 0, 2, 3
        data_rows, first_line = lines, 1
    elif len(lines[0]) == 3:
        qid_index, docid_index, grade_index = 0, 1, 2
        data_rows, first_line = lines, 1
    else:
        raise ValueError("unrecognized qrels header or row format")
    qrels: dict[str, dict[str, int]] = {}
    for number, row in enumerate(data_rows, start=first_line):
        if max(qid_index, docid_index, grade_index) >= len(row):
            raise ValueError(f"invalid qrels row {number}")
        qid, docid = row[qid_index].strip(), row[docid_index].strip()
        try:
            grade = int(row[grade_index])
        except ValueError as exc:
            raise ValueError(f"invalid qrels grade at row {number}") from exc
        if not qid or not docid or not 0 <= grade <= 3:
            raise ValueError(f"invalid qrels judgment at row {number}")
        case_qrels = qrels.setdefault(qid, {})
        if docid in case_qrels:
            raise ValueError(f"duplicate qrels judgment at row {number}")
        case_qrels[docid] = grade
    return qrels


def longbench_documents(samples: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return the only LongBench fields allowed to reach source ingestion."""
    documents: list[dict[str, str]] = []
    seen: set[str] = set()
    for sample in samples:
        sample_id, dataset, context = sample.get("_id"), sample.get("dataset"), sample.get("context")
        if not all(isinstance(value, str) and value.strip() for value in (sample_id, dataset, context)):
            raise ValueError("invalid LongBench sample")
        doc_id = f"{dataset}:{sample_id}"
        if doc_id in seen:
            raise ValueError("duplicate LongBench sample ID")
        seen.add(doc_id)
        documents.append({"doc_id": doc_id, "title": f"{dataset} {sample_id}", "text": context})
    return documents


def require_split_for_phase(split: str, phase: str) -> str:
    if split == "development" and phase in {"baseline", "optimize", "qa", "report"}:
        return split
    if split == "locked_holdout" and phase == "validate":
        return split
    if split == "locked_holdout":
        raise ValueError("locked split is validation-only")
    raise ValueError("unsupported split or phase")


def _validate_corpus(rows: list[dict[str, Any]]) -> tuple[PublicDocument, ...]:
    result: list[PublicDocument] = []
    seen: set[str] = set()
    for row in rows:
        if set(row) - {"doc_id", "title", "text"} or _FORBIDDEN_CORPUS_FIELDS & set(row):
            raise ValueError("corpus row contains non-source evaluation fields")
        doc_id, title, content = row.get("doc_id"), row.get("title", ""), row.get("text")
        if not isinstance(doc_id, str) or not doc_id or doc_id in seen:
            raise ValueError("invalid or duplicate corpus document ID")
        if not isinstance(title, str) or not isinstance(content, str) or not content.strip():
            raise ValueError("invalid corpus text")
        seen.add(doc_id)
        result.append(PublicDocument(doc_id=doc_id, title=title, text=content))
    if not result:
        raise ValueError("empty public corpus")
    return tuple(result)


def _validate_cases(rows: list[dict[str, Any]], split: str, document_ids: set[str]) -> tuple[PublicCase, ...]:
    result: list[PublicCase] = []
    seen: set[str] = set()
    for row in rows:
        qid, question, raw_qrels = row.get("qid"), row.get("question"), row.get("qrels")
        if not isinstance(qid, str) or not qid or qid in seen:
            raise ValueError("invalid or duplicate public qid")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("invalid public question")
        if row.get("split") != split:
            raise ValueError("public case split mismatch")
        if not isinstance(raw_qrels, dict) or any(
            not isinstance(doc_id, str) or doc_id not in document_ids or type(grade) is not int or not 0 <= grade <= 3
            for doc_id, grade in raw_qrels.items()
        ):
            raise ValueError("invalid or out-of-pool public qrels")
        answers = row.get("answers", [])
        if not isinstance(answers, list) or any(not isinstance(answer, str) for answer in answers):
            raise ValueError("invalid public reference answers")
        paired_source = row.get("paired_source_doc_id")
        if paired_source is not None and paired_source not in document_ids:
            raise ValueError("unknown paired LongBench source")
        result.append(PublicCase(
            qid=qid,
            question=question,
            qrels=dict(raw_qrels),
            split=split,
            answers=tuple(answers),
            paired_source_doc_id=paired_source,
            qrels_kind=row.get("qrels_kind", "official_document_qrels"),
        ))
        seen.add(qid)
    if not result:
        raise ValueError("empty public split")
    return tuple(result)


def _verify_manifest(dataset_dir: Path) -> dict[str, Any]:
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("prepared public dataset manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported prepared public dataset schema")
    for record in manifest.get("files", []):
        relative = Path(record.get("path", ""))
        path = (dataset_dir / relative).resolve()
        if not path.is_relative_to(dataset_dir.resolve()) or not path.is_file():
            raise ValueError("prepared data path is invalid")
        payload = path.read_bytes()
        if len(payload) != record.get("bytes") or _sha256(payload) != record.get("sha256"):
            raise ValueError("prepared public dataset file hash mismatch")
    source_files = manifest.get("source_files")
    if not isinstance(source_files, list) or not source_files:
        raise ValueError("prepared public source manifest is missing")
    for record in source_files:
        source_path = Path(record.get("path", ""))
        if not source_path.is_absolute() or not source_path.is_file():
            raise ValueError("public source cache path is invalid")
        payload = source_path.read_bytes()
        if len(payload) != record.get("bytes") or _sha256(payload) != record.get("sha256"):
            raise ValueError("public source cache hash mismatch")
    return manifest


def load_public_dataset(
    name: str,
    data_root: Path = DEFAULT_DATA_ROOT,
    *,
    split: str = "development",
    phase: str = "baseline",
) -> PublicDataset:
    """Load only the frozen prepared interchange format, enforcing split use."""
    require_split_for_phase(split, phase)
    if name not in DATASET_VERSIONS:
        raise ValueError("unsupported public dataset")
    dataset_dir = Path(data_root) / "prepared" / "adapters" / name
    manifest = _verify_manifest(dataset_dir)
    if manifest.get("dataset") != name or manifest.get("dataset_version") != DATASET_VERSIONS[name]:
        raise ValueError("prepared public dataset identity mismatch")
    documents = _validate_corpus(_jsonl_rows(dataset_dir / "corpus.jsonl"))
    cases = _validate_cases(_jsonl_rows(dataset_dir / "cases" / f"{split}.jsonl"), split,
                            {document.doc_id for document in documents})
    return PublicDataset(name, manifest["dataset_version"], split, manifest, documents, cases)


def prepare_public_datasets(data_root: Path = DEFAULT_DATA_ROOT) -> dict[str, Path]:
    """Build immutable, deterministic adapter files from the downloaded sources."""
    root = Path(data_root).resolve()
    source_records: dict[str, list[dict[str, Any]]] = {}
    prepared: dict[str, tuple[list[dict[str, str]], dict[str, list[dict[str, Any]]], dict[str, Any]]] = {}

    scifact_root = root / "prepared" / "scifact" / "scifact"
    scifact_docs = _jsonl_rows(scifact_root / "corpus.jsonl")
    scifact_doc_ids = {row.get("_id") for row in scifact_docs}
    scifact_queries = {row.get("_id"): row.get("text") for row in _jsonl_rows(scifact_root / "queries.jsonl")}
    scifact_cases: dict[str, list[dict[str, Any]]] = {}
    for filename, split in (("train.tsv", "development"), ("test.tsv", "locked_holdout")):
        qrels = parse_qrels_tsv((scifact_root / "qrels" / filename).read_text(encoding="utf-8"))
        cases = []
        for qid, judgments in qrels.items():
            if qid not in scifact_queries or set(judgments) - scifact_doc_ids:
                raise ValueError("SciFact qrels reference an unknown query or document")
            cases.append({"qid": qid, "question": scifact_queries[qid], "qrels": judgments, "split": split})
        scifact_cases[split] = sorted(cases, key=lambda case: case["qid"])
    scifact_sources = [scifact_root / name for name in ("corpus.jsonl", "queries.jsonl", "qrels/train.tsv", "qrels/test.tsv")]
    source_records["scifact"] = [_source_file_record(path) for path in scifact_sources]
    prepared["scifact"] = (
        [{"doc_id": row["_id"], "title": row.get("title", ""), "text": row["text"]} for row in scifact_docs],
        scifact_cases,
        {"source_revisions": {"BEIR": "ef83d29307061c65d04b035b4f4e7c18bd8374af", "SciFact": "68b98a56d93e0f9da0d2aab4e6c3294699a0f72e"},
         "source_licenses": {"labels": "CC BY 4.0", "abstracts": "ODC-By 1.0"}, "qrels_kind": "official_document_qrels"},
    )

    miracl_root = root / "prepared" / "miracl-zh-candidate-pool-v1"
    miracl_manifest = json.loads((miracl_root / "pool-manifest.json").read_text(encoding="utf-8"))
    miracl_docs = _jsonl_rows(miracl_root / "corpus.jsonl")
    miracl_cases: dict[str, list[dict[str, Any]]] = {}
    miracl_sources = [miracl_root / "pool-manifest.json", miracl_root / "corpus.jsonl"]
    for filename, source_split, split in (("train.jsonl", "train", "development"), ("dev.jsonl", "dev", "locked_holdout")):
        rows = _jsonl_rows(miracl_root / "cases" / filename)
        qrels = parse_qrels_tsv((miracl_root / "pool_qrels" / f"{source_split}.tsv").read_text(encoding="utf-8"))
        miracl_sources.extend((miracl_root / "cases" / filename, miracl_root / "pool_qrels" / f"{source_split}.tsv"))
        cases = []
        for row in rows:
            qid = row.get("qid")
            if qid not in qrels or row.get("qrels") != qrels[qid]:
                raise ValueError("MIRACL case and candidate-pool qrels mismatch")
            if set(qrels[qid]) - {doc.get("docid") for doc in miracl_docs}:
                raise ValueError("MIRACL qrels reference a document outside the fixed pool")
            cases.append({"qid": qid, "question": row["question"], "qrels": qrels[qid], "split": split,
                          "qrels_kind": "official_judgments_with_candidate_pool_scope"})
        miracl_cases[split] = sorted(cases, key=lambda case: case["qid"])
    source_records["miracl-zh"] = [_source_file_record(path) for path in miracl_sources]
    prepared["miracl-zh"] = (
        [{"doc_id": row["docid"], "title": row.get("title", ""), "text": row["text"]} for row in miracl_docs],
        miracl_cases,
        {"source_revisions": {"MIRACL topics/qrels": miracl_manifest["topics_qrels_revision"],
                              "MIRACL corpus": miracl_manifest["corpus_revision"]},
         "pool_id": miracl_manifest["pool_id"], "pool_hash": miracl_manifest["pool_corpus_sha256"],
         "sampling_seed": miracl_manifest["sampling_seed"], "selection_rule": miracl_manifest["selection_rule"],
         "candidate_documents": miracl_manifest["candidate_documents"],
         "excluded_queries": miracl_manifest["excluded_queries"],
         "source_licenses": miracl_manifest["source_licenses"],
         "qrels_kind": "official_judgments_with_candidate_pool_scope"},
    )

    longbench_root = root / "raw" / "longbench-zh"
    longbench_samples = []
    longbench_sources = []
    for filename, dataset in (("multifieldqa_zh.jsonl", "multifieldqa_zh"), ("dureader.jsonl", "dureader")):
        rows = _jsonl_rows(longbench_root / filename)
        if any(row.get("dataset") != dataset for row in rows):
            raise ValueError("LongBench subset identity mismatch")
        longbench_samples.extend(rows)
        longbench_sources.append(_source_file_record(longbench_root / filename))
    longbench_docs = longbench_documents(longbench_samples)
    doc_by_id = {row["doc_id"]: row for row in longbench_docs}
    longbench_cases: dict[str, list[dict[str, Any]]] = {"development": [], "locked_holdout": []}
    split_by_id: dict[str, str] = {}
    for dataset in ("multifieldqa_zh", "dureader"):
        subset = [row for row in longbench_samples if row["dataset"] == dataset]
        if len(subset) != 200:
            raise ValueError("LongBench subset sample count changed")
        ordered = sorted(subset, key=lambda row: hashlib.sha256(
            f"LongBench-ZH-SPLIT-V1\0seed={LONG_BENCH_SEED}\0{row['_id']}".encode("utf-8")
        ).hexdigest())
        for index, sample in enumerate(ordered):
            split = "development" if index < 160 else "locked_holdout"
            doc_id = f"{dataset}:{sample['_id']}"
            split_by_id[doc_id] = split
            case = {"qid": doc_id, "question": sample["input"], "answers": sample["answers"],
                    "paired_source_doc_id": doc_id, "qrels": {doc_id: 1}, "qrels_kind": "paired_task_context_diagnostic",
                    "split": split}
            longbench_cases[split].append(case)
    for split in longbench_cases:
        longbench_cases[split].sort(key=lambda case: case["qid"])
    source_records["longbench-zh"] = longbench_sources
    prepared["longbench-zh"] = (
        longbench_docs,
        longbench_cases,
        {"source_revisions": {"HuggingFace THUDM/LongBench": "5e628be450b7e67fb7ae6e201bd6d8f7056f7672"},
         "sampling_seed": LONG_BENCH_SEED, "split_rule": "Within each official subset, sort SHA256(LongBench-ZH-SPLIT-V1\\0seed=20260928\\0_id); first 160 development, remaining 40 locked_holdout.",
         "subset_counts": {"multifieldqa_zh": 200, "dureader": 200},
         "source_licenses": {"repository_code": "MIT", "underlying_data": "heterogeneous source rights; item-level terms unresolved"},
         "qrels_kind": "paired_task_context_diagnostic_not_official_qrels",
         "span_level_recall": "NOT_AVAILABLE"},
    )

    results: dict[str, Path] = {}
    output_root = root / "prepared" / "adapters"
    for name, (documents, cases_by_split, provenance) in prepared.items():
        dataset_dir = output_root / name
        dataset_dir.mkdir(parents=True, exist_ok=True)
        expected = {
            "corpus.jsonl": documents,
            "cases/development.jsonl": cases_by_split["development"],
            "cases/locked_holdout.jsonl": cases_by_split["locked_holdout"],
        }
        if name == "scifact":
            expected["cases/locked_holdout.jsonl"] = cases_by_split["locked_holdout"]
        for relative, rows in expected.items():
            path = dataset_dir / relative
            serialized = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
            if path.exists() and path.read_text(encoding="utf-8") != serialized:
                raise ValueError(f"refusing to overwrite changed prepared dataset: {path.name}")
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(serialized, encoding="utf-8", newline="\n")
        file_records = [_file_record(dataset_dir, dataset_dir / relative) for relative in sorted(expected)]
        manifest = {"schema_version": SCHEMA_VERSION, "dataset": name,
                    "dataset_version": DATASET_VERSIONS[name], "files": file_records,
                    "source_files": source_records[name], **provenance,
                    "split_counts": {split: len(rows) for split, rows in cases_by_split.items()},
                    "document_count": len(documents)}
        manifest_path = dataset_dir / "manifest.json"
        manifest_text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != manifest_text:
            raise ValueError(f"refusing to overwrite changed prepared manifest: {name}")
        if not manifest_path.exists():
            manifest_path.write_text(manifest_text, encoding="utf-8", newline="\n")
        _verify_manifest(dataset_dir)
        results[name] = dataset_dir
    return results

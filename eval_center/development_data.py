"""Load only the prepared corpus and Development cases for bounded validation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval_center.public_data import (
    DATASET_VERSIONS,
    SCHEMA_VERSION,
    PublicCase,
    PublicDocument,
    _jsonl_rows,
    _validate_cases,
    _validate_corpus,
)


@dataclass(frozen=True)
class DevelopmentDataset:
    name: str
    dataset_version: str
    split: str
    manifest_sha256: str
    corpus_sha256: str
    development_cases_sha256: str
    corpus_bytes: int
    development_cases_bytes: int
    documents: tuple[PublicDocument, ...]
    cases: tuple[PublicCase, ...]
    manifest_identity: dict[str, Any]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _record_for(manifest: dict[str, Any], relative_path: str) -> dict[str, Any]:
    records = [record for record in manifest.get("files", [])
               if isinstance(record, dict) and record.get("path") == relative_path]
    if len(records) != 1:
        raise ValueError("development_manifest_file_record_invalid")
    return records[0]


def load_development_dataset(name: str, data_root: Path) -> DevelopmentDataset:
    """Read exactly manifest.json, corpus.jsonl, and cases/development.jsonl.

    Other file records and source_files from the manifest are deliberately not
    opened or checksummed, including any Locked split artifacts.
    """
    if name not in DATASET_VERSIONS:
        raise ValueError("unsupported_public_dataset")
    dataset_dir = (Path(data_root) / "prepared" / "adapters" / name).resolve()
    manifest_path = dataset_dir / "manifest.json"
    corpus_path = dataset_dir / "corpus.jsonl"
    cases_path = dataset_dir / "cases" / "development.jsonl"
    for path in (manifest_path, corpus_path, cases_path):
        if not path.resolve().is_relative_to(dataset_dir) or not path.is_file():
            raise ValueError("development_data_path_invalid")

    manifest_bytes = manifest_path.read_bytes()
    corpus_bytes = corpus_path.read_bytes()
    cases_bytes = cases_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION
            or manifest.get("dataset") != name or manifest.get("dataset_version") != DATASET_VERSIONS[name]):
        raise ValueError("development_manifest_identity_mismatch")

    for relative_path, payload in (("corpus.jsonl", corpus_bytes),
                                   ("cases/development.jsonl", cases_bytes)):
        record = _record_for(manifest, relative_path)
        if record.get("bytes") != len(payload) or record.get("sha256") != _sha256(payload):
            raise ValueError("development_file_hash_mismatch")

    documents = _validate_corpus(_jsonl_rows(corpus_path))
    cases = _validate_cases(_jsonl_rows(cases_path), "development", {document.doc_id for document in documents})
    safe_identity = {
        "schema_version": manifest["schema_version"],
        "dataset": name,
        "dataset_version": manifest["dataset_version"],
        "split": "development",
        "manifest_sha256": _sha256(manifest_bytes),
        "corpus_sha256": _sha256(corpus_bytes),
        "development_cases_sha256": _sha256(cases_bytes),
    }
    return DevelopmentDataset(
        name=name,
        dataset_version=manifest["dataset_version"],
        split="development",
        manifest_sha256=safe_identity["manifest_sha256"],
        corpus_sha256=safe_identity["corpus_sha256"],
        development_cases_sha256=safe_identity["development_cases_sha256"],
        corpus_bytes=len(corpus_bytes),
        development_cases_bytes=len(cases_bytes),
        documents=documents,
        cases=cases,
        manifest_identity=safe_identity,
    )


__all__ = ["DevelopmentDataset", "load_development_dataset"]

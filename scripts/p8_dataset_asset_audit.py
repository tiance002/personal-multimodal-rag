"""Read-only byte/manifest audit for the frozen six-dataset V6 package.

This checks package-relative references and source/prepared artifact integrity.
It does not index, ingest, call models, open a database, or modify package data.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import io
import json
import re
import sys
from pathlib import PurePosixPath
from pathlib import Path
from typing import Any
import zipfile


EXPECTED_DATASETS = {
    "scifact": ("original_sources", 200),
    "miracl-zh": ("original_sources", 400),
    "longbench-zh": ("original_sources", 48),
    "hotpotqa": ("prepared_input", 73),
    "omnidocbench": ("prepared_input", 24),
    "mmlongbench-doc": ("prepared_input", 3),
}
TEXT_MEDIA = {"text/markdown", "text/plain", "text/x-markdown"}
LEAKAGE_KEY = re.compile(r"qid|gold|qrel|answer|question|supporting|label|provenance", re.I)
CHUNK_KEYS = {
    "status", "document_id", "version_id", "source_sha256",
    "normalized_content_sha256", "chunker_config_identity", "parents",
    "children", "diagnostics",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _walk_fields(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            yield child_path, key
            yield from _walk_fields(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_fields(child, f"{path}[{index}]")


def _load_loader(path: Path):
    spec = importlib.util.spec_from_file_location("p8_frozen_package_loader", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("PACKAGE_LOADER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verify_image(data: bytes, expected_format: str | None = None) -> dict[str, Any]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        actual = image.format
        image.verify()
        if expected_format and actual != expected_format:
            raise ValueError(f"IMAGE_FORMAT_MISMATCH:{expected_format}:{actual}")
        return {"format": actual, "width": image.width, "height": image.height}


def _verify_pdf(data: bytes) -> dict[str, Any]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data), strict=True)
    pages = len(reader.pages)
    if pages < 1:
        raise ValueError("PDF_HAS_NO_PAGES")
    return {"pages": pages}


def _check_source(data: bytes, media_type: str) -> dict[str, Any]:
    media = media_type.lower().split(";", 1)[0].strip()
    if media == "application/pdf":
        return {"check": "pypdf", **_verify_pdf(data)}
    if media in {"image/png", "image/jpeg", "image/jpg"}:
        expected = "PNG" if media == "image/png" else "JPEG"
        return {"check": "Pillow.verify", **_verify_image(data, expected)}
    if media.startswith("text/"):
        text = data.decode("utf-8", errors="strict")
        if not text:
            raise ValueError("EMPTY_UTF8_TEXT_SOURCE")
        return {"check": "utf8", "characters": len(text)}
    raise ValueError(f"UNSUPPORTED_SOURCE_MEDIA_TYPE:{media}")


def _check_attachment(data: bytes, ref: dict[str, Any]) -> dict[str, Any]:
    path = str(ref.get("path", "")).lower()
    if data.startswith(b"%PDF-") or path.endswith(".pdf"):
        return {"check": "pypdf", **_verify_pdf(data)}
    if data.startswith(b"\x89PNG\r\n\x1a\n") or path.endswith(".png"):
        return {"check": "Pillow.verify", **_verify_image(data, "PNG")}
    if data.startswith(b"\xff\xd8\xff") or path.endswith((".jpg", ".jpeg")):
        return {"check": "Pillow.verify", **_verify_image(data, "JPEG")}
    if path.endswith(".json"):
        value = json.loads(data.decode("utf-8", errors="strict"))
        if not isinstance(value, (dict, list)):
            raise ValueError("ATTACHMENT_JSON_TOP_LEVEL_INVALID")
        return {"check": "json", "top_level": type(value).__name__}
    value = data.decode("utf-8", errors="strict")
    if not value:
        raise ValueError("EMPTY_UTF8_ATTACHMENT")
    return {"check": "utf8", "characters": len(value)}


def _read_ref(loader, dataset_root: Path, ref: Any, label: str, archive=None):
    if not isinstance(ref, dict):
        raise ValueError(f"{label}:ARTIFACT_REF_NOT_OBJECT")
    if not isinstance(ref.get("path"), str) or not ref["path"]:
        raise ValueError(f"{label}:ARTIFACT_PATH_MISSING")
    if not isinstance(ref.get("bytes"), int) or ref["bytes"] < 0:
        raise ValueError(f"{label}:ARTIFACT_BYTES_INVALID")
    digest = ref.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"{label}:ARTIFACT_SHA256_INVALID")
    if archive is None:
        data = loader._read(dataset_root, ref)
    else:
        posix_path = PurePosixPath(ref["path"])
        if (posix_path.is_absolute() or "\\" in ref["path"] or ":" in ref["path"] or
                any(part in {".", ".."} for part in posix_path.parts)):
            raise ValueError(f"{label}:PACKAGE_UNSAFE_RELATIVE_PATH")
        info = archive.getinfo(posix_path.as_posix())
        if info.is_dir():
            raise ValueError(f"{label}:ARTIFACT_REF_IS_DIRECTORY")
        data = archive.read(info)
    if len(data) != ref["bytes"]:
        raise ValueError(f"{label}:ARTIFACT_BYTES_MISMATCH")
    if _sha(data) != digest:
        raise ValueError(f"{label}:ARTIFACT_HASH_MISMATCH")
    return data


def _mark_source_aliases(rows: list[dict[str, Any]]):
    hash_refs: dict[str, list[int]] = defaultdict(list)
    identity_refs: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        digest = row.get("source_sha256")
        source_id = row.get("source_document_id")
        if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
            hash_refs[digest].append(index)
        if isinstance(source_id, str) and source_id:
            identity_refs[source_id].append(index)
    duplicate_hash_groups = []
    for digest, row_indexes in sorted(hash_refs.items()):
        if len(row_indexes) > 1:
            keys = []
            for index in row_indexes:
                row = rows[index]
                row["duplicate_source_hash"] = True
                row["source_unique_asset"] = False
                keys.append({"dataset": row["dataset"], "unit_id": row["unit_id"]})
            duplicate_hash_groups.append({"sha256": digest, "units": keys})
    duplicate_identity_groups = []
    for source_id, row_indexes in sorted(identity_refs.items()):
        if len(row_indexes) > 1:
            keys = []
            for index in row_indexes:
                row = rows[index]
                row["duplicate_source_identity"] = True
                row["source_unique_asset"] = False
                keys.append({"dataset": row["dataset"], "unit_id": row["unit_id"]})
            duplicate_identity_groups.append({"source_document_id": source_id, "units": keys})
    return duplicate_hash_groups, duplicate_identity_groups


def audit(package_root: Path, output_dir: Path, pypdf_site: Path | None,
          replace_existing_outputs: bool = False) -> dict[str, Any]:
    if pypdf_site is not None:
        sys.path.append(str(pypdf_site))
    package_root = package_root.resolve(strict=True)
    output_dir = output_dir.resolve()
    index_path = package_root / "six-package-manifest.json"
    index_bytes = index_path.read_bytes()
    index_sha = _sha(index_bytes)
    index = json.loads(index_bytes.decode("utf-8", errors="strict"))
    if index.get("schema") != "rag-v6-final-six-pack-index/v1":
        raise ValueError("SIX_PACKAGE_INDEX_SCHEMA_INVALID")
    loader = _load_loader(package_root / "package_loader.py")

    # Treat the manifest index as authoritative, while requiring the six frozen counts.
    packages = index.get("packages")
    if not isinstance(packages, list):
        raise ValueError("SIX_PACKAGE_INDEX_PACKAGES_INVALID")
    observed = {entry.get("dataset") for entry in packages if isinstance(entry, dict)}
    if observed != set(EXPECTED_DATASETS):
        raise ValueError("SIX_DATASET_SET_MISMATCH")

    unit_rows: list[dict[str, Any]] = []
    dataset_counts: dict[str, dict[str, Any]] = {}
    manifest_digests: dict[str, str] = {}
    identity_refs: dict[tuple[str, str], list[int]] = defaultdict(list)
    source_row_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    field_paths: set[str] = set()
    field_names: set[str] = set()
    exceptions: list[dict[str, Any]] = []

    for package in packages:
        dataset = package["dataset"]
        expected_kind, expected_count = EXPECTED_DATASETS[dataset]
        manifest_ref = package.get("manifest")
        if not isinstance(manifest_ref, dict):
            raise ValueError(f"{dataset}:INDEX_MANIFEST_REF_INVALID")
        relative_manifest = manifest_ref.get("path")
        if not isinstance(relative_manifest, str):
            raise ValueError(f"{dataset}:INDEX_MANIFEST_PATH_MISSING")
        manifest_path = package_root.joinpath(*relative_manifest.replace("\\", "/").split("/"))
        manifest_bytes = manifest_path.read_bytes()
        digest = _sha(manifest_bytes)
        manifest_digests[dataset] = digest
        if digest != manifest_ref.get("sha256") or len(manifest_bytes) != manifest_ref.get("bytes"):
            exceptions.append({"scope": "manifest", "dataset": dataset, "reason": "INDEX_MANIFEST_PIN_MISMATCH"})
        manifest = json.loads(manifest_bytes.decode("utf-8", errors="strict"))
        archive_ref = package.get("archive")
        archive_path = package_root / archive_ref["path"] if isinstance(archive_ref, dict) else None
        archive_handle = None
        if archive_path is None or not archive_path.is_file():
            exceptions.append({"scope": "archive", "dataset": dataset, "reason": "ARCHIVE_MISSING"})
        else:
            archive_hash = hashlib.sha256()
            with archive_path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    archive_hash.update(block)
            if archive_hash.hexdigest() != archive_ref.get("sha256") or archive_path.stat().st_size != archive_ref.get("bytes"):
                exceptions.append({"scope": "archive", "dataset": dataset, "reason": "INDEX_ARCHIVE_PIN_MISMATCH"})
            try:
                archive_handle = zipfile.ZipFile(archive_path, "r")
                names = archive_handle.namelist()
                if len(names) != len(set(names)):
                    exceptions.append({"scope": "archive", "dataset": dataset, "reason": "DUPLICATE_ARCHIVE_PATH"})
                archive_manifest = archive_handle.read("manifest.json")
                if _sha(archive_manifest) != digest:
                    exceptions.append({"scope": "archive", "dataset": dataset, "reason": "ARCHIVE_MANIFEST_DIFFERS_FROM_INDEXED_MANIFEST"})
            except Exception as exc:
                exceptions.append({"scope": "archive", "dataset": dataset,
                                   "reason": f"ARCHIVE_OPEN:{type(exc).__name__}:{exc}"})
                if archive_handle is not None:
                    archive_handle.close()
                archive_handle = None
        for field_path, field_name in _walk_fields(manifest):
            if LEAKAGE_KEY.search(field_name):
                field_paths.add(field_path)
                field_names.add(field_name)
        if manifest.get("dataset") != dataset or manifest.get("input_kind") != expected_kind:
            exceptions.append({"scope": "manifest", "dataset": dataset, "reason": "MANIFEST_DATASET_OR_KIND_MISMATCH"})
        units = manifest.get("units")
        if not isinstance(units, list):
            exceptions.append({"scope": "manifest", "dataset": dataset, "reason": "UNITS_NOT_ARRAY"})
            units = []
        if len(units) != expected_count or package.get("units") != expected_count:
            exceptions.append({"scope": "manifest", "dataset": dataset, "reason": "UNIT_COUNT_MISMATCH",
                               "expected": expected_count, "actual": len(units)})
        dataset_seen: set[str] = set()
        passed = 0
        failed = 0
        dataset_exceptions = 0
        for unit_index, unit in enumerate(units):
            row: dict[str, Any] = {
                "dataset": dataset,
                "unit_index": unit_index,
                "unit_id": unit.get("unit_id") if isinstance(unit, dict) else None,
                "input_kind": unit.get("input_kind") if isinstance(unit, dict) else None,
                "source_document_id": None,
                "source_sha256": None,
                "source_bytes": None,
                "source_media_type": None,
                "status": "PASS",
                "checks": [],
                "exceptions": [],
                "related_asset_count": 0,
                "related_asset_hashes": [],
                "duplicate_source_hash": False,
                "duplicate_source_identity": False,
                "source_unique_asset": True,
                "manifest_field_paths": [],
            }
            errors = row["exceptions"]
            if not isinstance(unit, dict):
                errors.append("UNIT_NOT_OBJECT")
            else:
                unit_id = unit.get("unit_id")
                if not isinstance(unit_id, str) or not unit_id:
                    errors.append("UNIT_ID_MISSING")
                elif unit_id in dataset_seen:
                    errors.append("DUPLICATE_UNIT_ID_WITHIN_DATASET")
                else:
                    dataset_seen.add(unit_id)
                if unit.get("input_kind") != expected_kind:
                    errors.append("UNIT_INPUT_KIND_MISMATCH")
                source = unit.get("source")
                if not isinstance(source, dict):
                    errors.append("SOURCE_NOT_OBJECT")
                else:
                    row["source_document_id"] = source.get("source_document_id")
                    row["source_media_type"] = source.get("media_type")
                    artifact = source.get("artifact")
                    if isinstance(artifact, dict):
                        row["source_sha256"] = artifact.get("sha256")
                        row["source_bytes"] = artifact.get("bytes")
                        try:
                            data = _read_ref(loader, package_root / Path(relative_manifest).parent, artifact,
                                             "source", archive=archive_handle if expected_kind == "original_sources" else None)
                            row["checks"].append({"artifact": "source", "path": artifact["path"],
                                                  "sha256": _sha(data), "bytes": len(data)})
                            source_check = _check_source(data, str(source.get("media_type", "")))
                            row["checks"].append({"artifact": "source_content", **source_check})
                        except Exception as exc:  # audit every remaining unit despite local failures
                            errors.append(f"SOURCE_CHECK:{type(exc).__name__}:{exc}")
                    else:
                        errors.append("SOURCE_ARTIFACT_REF_MISSING")
                prepared = unit.get("prepared")
                if expected_kind == "prepared_input":
                    if not isinstance(prepared, dict):
                        errors.append("PREPARED_OBJECT_MISSING")
                    else:
                        for key in ("normalized_json", "chunks_json", "assets_json"):
                            ref = prepared.get(key)
                            try:
                                data = _read_ref(loader, package_root / Path(relative_manifest).parent,
                                                 ref, key)
                                decoded = data.decode("utf-8", errors="strict")
                                parsed = json.loads(decoded)
                                if key == "normalized_json" and not isinstance(parsed, dict):
                                    raise ValueError("NORMALIZED_JSON_TOP_LEVEL_INVALID")
                                if key == "chunks_json":
                                    if not isinstance(parsed, dict) or set(parsed) != CHUNK_KEYS:
                                        raise ValueError("CHUNKS_JSON_SCHEMA_INVALID")
                                    if not isinstance(parsed.get("parents"), list) or not isinstance(parsed.get("children"), list):
                                        raise ValueError("CHUNKS_JSON_ARRAYS_INVALID")
                                if key == "assets_json" and not isinstance(parsed, list):
                                    raise ValueError("ASSETS_JSON_TOP_LEVEL_INVALID")
                                row["checks"].append({"artifact": key, "path": ref["path"],
                                                      "sha256": _sha(data), "bytes": len(data),
                                                      "json_top_level": type(parsed).__name__})
                            except Exception as exc:
                                errors.append(f"{key.upper()}_CHECK:{type(exc).__name__}:{exc}")
                        attachments = prepared.get("attachments")
                        if not isinstance(attachments, list):
                            errors.append("ATTACHMENTS_NOT_ARRAY")
                            attachments = []
                        for attachment_index, ref in enumerate(attachments):
                            try:
                                data = _read_ref(loader, package_root / Path(relative_manifest).parent,
                                                 ref, f"attachment[{attachment_index}]")
                                check = _check_attachment(data, ref)
                                row["related_asset_count"] += 1
                                row["related_asset_hashes"].append(ref["sha256"])
                                row["checks"].append({"artifact": "attachment", "path": ref["path"],
                                                      "sha256": _sha(data), "bytes": len(data), **check})
                            except Exception as exc:
                                errors.append(f"ATTACHMENT[{attachment_index}]_CHECK:{type(exc).__name__}:{exc}")
                elif prepared is not None:
                    errors.append("RAW_UNIT_HAS_PREPARED_PAYLOAD")

            unit_key = row.get("unit_id")
            if isinstance(unit_key, str):
                identity_refs[(dataset, unit_key)].append(len(unit_rows))
            row["manifest_field_paths"] = sorted(path for path, name in _walk_fields(unit)
                                                  if LEAKAGE_KEY.search(name)) if isinstance(unit, dict) else []
            if errors:
                row["status"] = "FAIL"
                failed += 1
                dataset_exceptions += len(errors)
                exceptions.extend({"scope": "unit", "dataset": dataset,
                                   "unit_id": row["unit_id"], "reason": error} for error in errors)
            else:
                passed += 1
            unit_rows.append(row)
            if isinstance(unit_key, str):
                source_row_by_key[(dataset, unit_key)] = row
        dataset_counts[dataset] = {
            "input_kind": expected_kind,
            "manifest_units": len(units),
            "expected_units": expected_count,
            "valid_units": passed,
            "invalid_units": failed,
            "exception_count": dataset_exceptions,
            "source_media_types": dict(Counter(row["source_media_type"] for row in unit_rows
                                                if row["dataset"] == dataset)),
            "referenced_attachment_count": sum(row["related_asset_count"] for row in unit_rows
                                                if row["dataset"] == dataset),
        }
        if archive_handle is not None:
            archive_handle.close()

    # Cross-dataset source aliases are called out explicitly and are never counted as independent QA.
    duplicate_hash_groups, duplicate_identity_groups = _mark_source_aliases(unit_rows)
    duplicate_unit_ids = {f"{dataset}:{unit_id}": indexes for (dataset, unit_id), indexes in identity_refs.items()
                         if len(indexes) > 1}
    duplicate_unit_index_set = {i for indexes in duplicate_unit_ids.values() for i in indexes}
    for index in duplicate_unit_index_set:
        unit_rows[index]["status"] = "FAIL"
        unit_rows[index]["exceptions"].append("DUPLICATE_GLOBAL_UNIT_IDENTITY")
        exceptions.append({"scope": "unit", "dataset": unit_rows[index]["dataset"],
                           "unit_id": unit_rows[index]["unit_id"], "reason": "DUPLICATE_GLOBAL_UNIT_IDENTITY"})
    for dataset, counts in dataset_counts.items():
        rows = [row for row in unit_rows if row["dataset"] == dataset]
        counts["valid_units"] = sum(row["status"] == "PASS" for row in rows)
        counts["invalid_units"] = len(rows) - counts["valid_units"]
        counts["exception_count"] += sum("DUPLICATE_GLOBAL_UNIT_IDENTITY" in row["exceptions"] for row in rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    units_path = output_dir / "units.jsonl"
    summary_path = output_dir / "asset-audit.json"
    if (units_path.exists() or summary_path.exists()) and not replace_existing_outputs:
        raise FileExistsError("P8_AUDIT_OUTPUT_ALREADY_EXISTS")
    final_valid = sum(row["status"] == "PASS" for row in unit_rows)
    final_invalid = len(unit_rows) - final_valid
    summary = {
        "schema": "p8-six-dataset-asset-audit/v1",
        "status": "PASS" if not exceptions and final_invalid == 0 else "FAIL",
        "package_root": str(package_root),
        "index_path": str(index_path),
        "index_sha256": index_sha,
        "dataset_manifest_sha256": manifest_digests,
        "expected_total_units": 748,
        "observed_total_units": len(unit_rows),
        "valid_units": final_valid,
        "invalid_units": final_invalid,
        "unique_source_units": sum(bool(row["source_unique_asset"]) for row in unit_rows),
        "qa_independence": "NOT_ASSESSED",
        "independence_limit": "Unique source hashes/identities establish asset deduplication only; they do not establish independent QA items or Gold independence.",
        "duplicate_unit_id_groups": duplicate_unit_ids,
        "duplicate_source_hash_groups": duplicate_hash_groups,
        "duplicate_source_identity_groups": duplicate_identity_groups,
        "datasets": dataset_counts,
        "exceptions": exceptions,
        "manifest_qid_gold_provenance_field_paths": sorted(field_paths),
        "manifest_qid_gold_provenance_field_names": sorted(field_names, key=str.lower),
        "semantic_evidence_content_read": False,
        "gold_presence_used_for_asset_validity": False,
        "db_calls": 0,
        "model_calls": 0,
        "indexing_or_ingestion": "NOT_RUN",
        "source_package_modified": False,
    }
    units_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                                             for row in unit_rows), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path,
                        default=Path(r"D:\RAG-v6-final-ingestion-pack-r1\attempt3-ocr-layout"))
    parser.add_argument("--output-dir", type=Path, default=Path(r"E:\WK\run\p8-20261016"))
    parser.add_argument("--pypdf-site", type=Path,
                        help="optional installed site-packages directory containing pypdf")
    parser.add_argument("--replace-existing-outputs", action="store_true",
                        help="replace this audit's existing asset-audit.json and units.jsonl")
    args = parser.parse_args(argv)
    try:
        summary = audit(args.package_root, args.output_dir, args.pypdf_site,
                        replace_existing_outputs=args.replace_existing_outputs)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}:{exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps({
        "status": summary["status"],
        "index_sha256": summary["index_sha256"],
        "observed_total_units": summary["observed_total_units"],
        "valid_units": summary["valid_units"],
        "invalid_units": summary["invalid_units"],
        "unique_source_units": summary["unique_source_units"],
        "qa_independence": summary["qa_independence"],
        "datasets": {name: {"input_kind": item["input_kind"], "manifest_units": item["manifest_units"],
                            "valid_units": item["valid_units"], "invalid_units": item["invalid_units"],
                            "exception_count": item["exception_count"]}
                     for name, item in summary["datasets"].items()},
        "summary_path": str(args.output_dir / "asset-audit.json"),
        "units_path": str(args.output_dir / "units.jsonl"),
    }, ensure_ascii=False))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

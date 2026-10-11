"""Prepare an allowlisted, hash-verified P7 ECS input bundle.

Default behavior is read-only dry-run. This tool never connects to ECS, reads
provider credentials, or packages repository source, holdouts, ledgers, env
files, virtual environments, or private corpora.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = REPOSITORY_ROOT / "var/reports/v6-first-evaluation-20261010"
SOURCE_MANIFEST = REPORT_ROOT / "source_manifest.json"
COHORT_FILE = REPORT_ROOT / "cohort_dev24.jsonl"
CAPACITY_MANIFEST = REPOSITORY_ROOT / "var/reports/v6-text-capacity-r1/ecs-linux-resource-manifest.json"
PUBLIC_DATA_ROOT = Path(r"D:\RAG-Public-Bench")
CAPACITY_DATA_ROOT = Path(r"D:\RAG-v6-text-capacity-r1")
HARD_DEADLINE_UTC = "2026-10-10T04:25:44Z"
TARGET_ROOT = "/opt/rag-p7-r1"
DATASETS = ("scifact", "miracl-zh", "longbench-zh")


class BundleError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _record(role: str, source: Path, archive_path: str,
            expected_bytes: int | None = None,
            expected_sha256: str | None = None,
            allowed_root: Path | None = None) -> dict[str, Any]:
    if source.is_symlink() or not source.is_file():
        raise BundleError(f"allowlisted_source_missing_or_symlink:{role}")
    resolved = source.resolve(strict=True)
    if allowed_root is not None and not resolved.is_relative_to(allowed_root.resolve(strict=True)):
        raise BundleError(f"source_outside_allowlisted_root:{role}")
    size = resolved.stat().st_size
    digest = _sha256(resolved)
    if expected_bytes is not None and size != expected_bytes:
        raise BundleError(f"source_size_mismatch:{role}")
    if expected_sha256 is not None and digest != expected_sha256:
        raise BundleError(f"source_sha256_mismatch:{role}")
    return {
        "role": role,
        "local_path": str(resolved),
        "archive_path": archive_path,
        "bytes": size,
        "sha256": digest,
    }


def collect_allowlisted_inputs() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from eval_center.paired_state import load_frozen_cohort

    if not SOURCE_MANIFEST.is_file() or not COHORT_FILE.is_file() or not CAPACITY_MANIFEST.is_file():
        raise BundleError("required_frozen_manifest_or_cohort_missing")
    source_manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    if source_manifest.get("status") != "FROZEN_FOR_PREPARATION":
        raise BundleError("source_manifest_not_frozen")
    cohort = load_frozen_cohort(COHORT_FILE)
    counts = {name: sum(row["dataset"] == name for row in cohort) for name in DATASETS}
    if len(cohort) != 24 or counts != {name: 8 for name in DATASETS}:
        raise BundleError("cohort_must_be_frozen_dev24")

    dataset_records = {item.get("dataset"): item for item in source_manifest.get("datasets", [])}
    if set(dataset_records) != set(DATASETS):
        raise BundleError("source_manifest_dataset_set_invalid")

    entries: list[dict[str, Any]] = []
    for dataset in DATASETS:
        item = dataset_records[dataset]
        for field, archive_name in (
            ("corpus", f"inputs/public/{dataset}/corpus.jsonl"),
            ("source_manifest", f"inputs/public/{dataset}/manifest.json"),
        ):
            record = item[field]
            path = Path(record["path"])
            entries.append(_record(
                f"{dataset}:{field}", path, archive_name,
                expected_bytes=record["bytes"], expected_sha256=record["sha256"],
                allowed_root=PUBLIC_DATA_ROOT,
            ))

    entries.append(_record(
        "selected_dev24_qrels_and_answer_gold", COHORT_FILE,
        "inputs/evaluation/cohort_dev24.jsonl",
        allowed_root=REPORT_ROOT,
    ))
    entries.append(_record(
        "frozen_source_manifest", SOURCE_MANIFEST,
        "inputs/evaluation/source_manifest.json",
        allowed_root=REPORT_ROOT,
    ))

    capacity = json.loads(CAPACITY_MANIFEST.read_text(encoding="utf-8"))
    if capacity.get("readiness") != "PREPARED_NOT_TARGET_VERIFIED":
        raise BundleError("capacity_manifest_readiness_unexpected")
    package = capacity["package"]
    if package.get("target", {}).get("os") != "Linux" or package.get("target", {}).get("architecture") != "x86_64":
        raise BundleError("capacity_wheel_target_mismatch")
    entries.append(_record(
        "pinned_linux_wheel", Path(package["local_staging_path"]),
        "inputs/runtime/linux/" + package["wheel_filename"],
        expected_bytes=package["bytes"], expected_sha256=package["sha256"],
        allowed_root=CAPACITY_DATA_ROOT,
    ))
    tokenizer = capacity["tokenizer"]
    entries.append(_record(
        "pinned_tokenizer", Path(tokenizer["local_staging_path"]),
        "inputs/runtime/linux/tokenizer-v41.json",
        expected_bytes=tokenizer["bytes"], expected_sha256=tokenizer["sha256"],
        allowed_root=CAPACITY_DATA_ROOT,
    ))

    if len({item["archive_path"] for item in entries}) != len(entries):
        raise BundleError("archive_path_duplicate")
    forbidden = ("locked_holdout", "holdout", ".env", "venv", ".git", "ledger", "private")
    if any(any(part.lower() in forbidden for part in Path(row["archive_path"]).parts) for row in entries):
        raise BundleError("forbidden_path_in_bundle")

    manifest = {
        "schema_version": "p7-ecs-allowlisted-inputs-v1",
        "readiness": "PREPARED_NOT_UPLOADED",
        "target_root": TARGET_ROOT,
        "target_instance_id": "i-bp1flliql08bz8yftild",
        "target_region": "cn-hangzhou",
        "hard_deadline_utc": HARD_DEADLINE_UTC,
        "source_manifest_sha256": _sha256(SOURCE_MANIFEST),
        "cohort_sha256": _sha256(COHORT_FILE),
        "cohort_qid_count": len(cohort),
        "cohort_counts": counts,
        "included_entries": entries,
        "excluded": ["locked_holdout contents", "unselected development files", "repository source",
                     "credentials and .env", "virtual environments", "historical cost ledger",
                     "private corpora", "services and database dumps"],
        "upload_or_execution": "NOT_RUN",
        "identity_and_unique_job_gate": "REQUIRED_BEFORE_DEPLOYMENT",
    }
    return manifest, entries


def _build_zip(entries: list[dict[str, Any]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for entry in entries:
            archive.write(entry["local_path"], entry["archive_path"])
    return buffer.getvalue()


def _write_new(path: Path, payload: bytes) -> None:
    resolved = path.resolve()
    report = REPORT_ROOT.resolve()
    if not resolved.is_relative_to(report):
        raise BundleError("output_must_be_inside_v6_report_directory")
    if resolved.exists():
        raise BundleError("refusing_to_overwrite_existing_output")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("xb") as stream:
        stream.write(payload)


def _validate_new_output(path: Path) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(REPORT_ROOT.resolve()):
        raise BundleError("output_must_be_inside_v6_report_directory")
    if resolved.exists():
        raise BundleError("refusing_to_overwrite_existing_output")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--write-manifest", action="store_true",
                         help="write an allowlisted upload manifest under the v6 report directory")
    actions.add_argument("--build-archive", action="store_true",
                         help="write the exact allowlisted ZIP and its manifest; still does not upload")
    parser.add_argument("--manifest-output", type=Path,
                        default=REPORT_ROOT / "ecs-upload-manifest.json")
    parser.add_argument("--archive-output", type=Path,
                        default=REPORT_ROOT / "p7-ecs-allowlisted-inputs.zip")
    args = parser.parse_args(argv)
    try:
        manifest, entries = collect_allowlisted_inputs()
        if args.write_manifest or args.build_archive:
            _validate_new_output(args.manifest_output)
        archive_bytes = _build_zip(entries) if args.build_archive else None
        if archive_bytes is not None:
            _validate_new_output(args.archive_output)
            manifest["archive"] = {
                "path": str(args.archive_output.resolve()),
                "bytes": len(archive_bytes),
                "sha256": hashlib.sha256(archive_bytes).hexdigest(),
            }
            _write_new(args.archive_output, archive_bytes)
        manifest_bytes = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        if args.write_manifest or args.build_archive:
            _write_new(args.manifest_output, manifest_bytes)
            output_status = "MANIFEST_WRITTEN_BUNDLE_NOT_UPLOADED"
        else:
            output_status = "DRY_RUN_NO_FILES_WRITTEN"
        print(json.dumps({
            "status": output_status,
            "target_root": manifest["target_root"],
            "target_instance_id": manifest["target_instance_id"],
            "hard_deadline_utc": manifest["hard_deadline_utc"],
            "entries": len(entries),
            "total_bytes": sum(item["bytes"] for item in entries),
            "cohort_qid_count": manifest["cohort_qid_count"],
            "cohort_counts": manifest["cohort_counts"],
            "archive_sha256": manifest.get("archive", {}).get("sha256"),
            "upload_or_execution": "NOT_RUN",
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_code": type(exc).__name__,
                          "reason": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

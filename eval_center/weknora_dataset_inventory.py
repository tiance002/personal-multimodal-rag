"""P7 source inventory only; no old QuickChain, grants or model runtime imports."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from eval_center.public_data import DATASET_VERSIONS, _verify_manifest
from eval_center.special_public_data import load_special_datasets
from eval_center.omni_contract import page_input_contract

SIX_DATASETS = ("scifact", "miracl-zh", "longbench-zh",
                "hotpotqa", "mmlongbench-doc", "omnidocbench")


def source_inventory(data_root, *, freeze_path=None, freeze_sha256=None, asset_root=None):
    """Validate existing assets without indexing Gold or inventing missing QA."""
    root = Path(data_root)
    rows = []
    for name in SIX_DATASETS[:3]:
        directory = root / "prepared" / "adapters" / name
        if not directory.is_dir():
            rows.append({"dataset": name, "status": "BLOCKED_ASSETS_MISSING",
                         "rag_execution": "NOT_RUN"})
            continue
        manifest = _verify_manifest(directory)
        if manifest.get("dataset") != name or manifest.get("dataset_version") != DATASET_VERSIONS[name]:
            raise ValueError("PUBLIC_DATASET_IDENTITY_MISMATCH")
        rows.append({"dataset": name, "dataset_version": manifest["dataset_version"],
                     "status": "SOURCE_ASSETS_VERIFIED", "rag_execution": "NOT_RUN",
                     "gold_boundary": "unchanged existing adapter; qrels/answers never corpus"})
    if freeze_path is None:
        rows.extend({"dataset": name, "status": "BLOCKED_EXPLICIT_FREEZE_REQUIRED",
                     "rag_execution": "NOT_RUN"} for name in SIX_DATASETS[3:])
    else:
        if not isinstance(freeze_sha256, str) or len(freeze_sha256) != 64:
            raise ValueError("EXPLICIT_FREEZE_SHA256_REQUIRED")
        special = load_special_datasets(freeze_path, expected_sha256=freeze_sha256,
                                        asset_root=asset_root)
        for name in SIX_DATASETS[3:5]:
            dataset = special[name]
            rows.append({"dataset": name, "dataset_version": dataset.dataset_version,
                         "status": "SOURCE_ASSETS_VERIFIED", "rag_execution": "NOT_RUN",
                         "question_count": len(dataset.cases),
                         "ingestion": "ORIGINAL_FULL_PDF" if name == "mmlongbench-doc" else "FULL_SOURCE_TEXT",
                         "gold_boundary": "native answers/supporting facts are evaluation-only"})
        omni = page_input_contract(freeze_path, expected_sha256=freeze_sha256, asset_root=asset_root)
        rows.append({"dataset": "omnidocbench", "status": "SOURCE_ASSETS_VERIFIED",
                     "page_count": omni["page_count"], "official_qa_count": 0,
                     "rag_execution": "BLOCKED_INDEPENDENT_PREMODEL_QA_REQUIRED",
                     "answer_accuracy": {"value": None, "status": "N/A_NO_OFFICIAL_QA"},
                     "gold_boundary": "page Gold is evaluation-only, not image/PDF input"})
    return {"schema": "weknora-public-source-inventory-v1", "datasets": rows,
            "source_status": "PARTIAL" if any(r["status"].startswith("BLOCKED") for r in rows) else "VERIFIED",
            "p7_full_chain_status": "NOT_RUN", "real_provider_calls": 0,
            "legacy_execution_grants_loaded": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--freeze-path")
    parser.add_argument("--freeze-sha256")
    parser.add_argument("--asset-root")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report = source_inventory(args.data_root, freeze_path=args.freeze_path,
                              freeze_sha256=args.freeze_sha256, asset_root=args.asset_root)
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"source_status": report["source_status"],
                      "p7_full_chain_status": "NOT_RUN", "report": str(path)}))
    return 0 if report["source_status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())


"""Development-only candidate selection with paired bootstrap intervals."""
from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any


PRIMARY_METRIC = "hybrid.document_metrics_at_10.ndcg_at_k"
SECONDARY_METRICS = (
    "hybrid.document_metrics_at_5.recall_at_k",
    "hybrid.context_document_metrics_at_5.recall_at_k",
)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _quantile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    left, right = math.floor(index), math.ceil(index)
    return ordered[left] + (ordered[right] - ordered[left]) * (index - left)


def _metric(metrics: dict[str, Any], name: str) -> float:
    value = metrics.get(name)
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"development metric unavailable: {name}")
    return float(value)


def _case_metric(case: dict[str, Any], variant_id: str) -> float | None:
    try:
        value = case["variants"][variant_id]["metrics"]["hybrid"][PRIMARY_METRIC]
    except (KeyError, TypeError):
        return None
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def _paired_bootstrap(rows: list[dict[str, Any]], base: str, candidate: str, *, seed: int,
                      repetitions: int = 2000) -> dict[str, Any]:
    paired = [(left, right) for row in rows
              if (left := _case_metric(row, base)) is not None
              and (right := _case_metric(row, candidate)) is not None]
    if not paired:
        raise ValueError("no paired development rows for candidate")
    differences = [right - left for left, right in paired]
    rng = random.Random(seed)
    boot = []
    for _ in range(repetitions):
        boot.append(sum(differences[rng.randrange(len(differences))] for _ in differences) / len(differences))
    return {
        "paired_cases": len(paired),
        "mean_delta": sum(differences) / len(differences),
        "confidence_level": 0.95,
        "bootstrap_repetitions": repetitions,
        "bootstrap_seed": seed,
        "ci_95": [_quantile(boot, 0.025), _quantile(boot, 0.975)],
    }


def select_development_candidate(run_dir: Path, data_root: Path) -> dict[str, Any]:
    run_dir, data_root = Path(run_dir), Path(data_root)
    manifest = _read(run_dir / "manifest.json")
    report = _read(run_dir / "report.json")
    if manifest.get("split") != "development" or manifest.get("profile") != "standard":
        raise ValueError("candidate selection requires a standard development run")
    if report.get("manifest", {}).get("run_hash") != manifest.get("run_hash"):
        raise ValueError("run report and manifest differ")
    variant_metrics = report.get("metrics", {})
    configs = manifest.get("effective_configs", {})
    if set(variant_metrics) != set(configs) or not configs:
        raise ValueError("run variants and effective configurations differ")
    ranked = []
    for variant_id, config in configs.items():
        metrics = variant_metrics[variant_id]["hybrid"]["metrics"]
        primary = _metric(metrics, PRIMARY_METRIC)
        secondary = [_metric(metrics, name) for name in SECONDARY_METRICS]
        latency = metrics.get("retrieval_ms")
        latency = float(latency) if type(latency) in (int, float) and math.isfinite(latency) else float("inf")
        ranked.append(((-primary, -secondary[0], -secondary[1], latency, variant_id), variant_id, config))
    _, selected_id, selected_config = min(ranked, key=lambda item: item[0])
    baseline_id = next(iter(configs))
    rows = [json.loads(line) for line in (run_dir / "cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    seed = int(hashlib.sha256(f"{manifest['dataset_version']}\0{manifest['corpus_hash']}".encode()).hexdigest()[:8], 16)
    comparisons = {
        variant_id: _paired_bootstrap(rows, baseline_id, variant_id, seed=seed)
        for variant_id in configs
    }
    selection = {
        "schema_version": "public-candidate-v1",
        "dataset": manifest["dataset"],
        "dataset_version": manifest["dataset_version"],
        "split": "development",
        "profile": "standard",
        "source_run": str(run_dir.resolve()),
        "source_run_manifest_sha256": _digest(run_dir / "manifest.json"),
        "git_sha": manifest["git_sha"],
        "corpus_hash": manifest["corpus_hash"],
        "qrels_hash": manifest["qrels_hash"],
        "selection_metric": PRIMARY_METRIC,
        "candidate_variant": selected_id,
        "candidate_config": selected_config,
        "candidate_status": "opt_in_pending_locked_validation",
        "comparisons_vs_first_variant": comparisons,
    }
    destination = data_root / "runs" / "candidates" / manifest["dataset"] / f"{manifest['experiment_id']}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError("candidate selection output already exists")
    destination.write_text(json.dumps(selection, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8", newline="\n")
    return {"status": "DEVELOPMENT_CANDIDATE_SELECTED", "dataset": manifest["dataset"],
            "candidate_variant": selected_id, "candidate_config": selected_config,
            "selection_metric": PRIMARY_METRIC, "selection_path": str(destination),
            "comparisons_vs_first_variant": comparisons}

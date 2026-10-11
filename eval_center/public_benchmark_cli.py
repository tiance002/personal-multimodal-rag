"""Command-line phases for the public benchmark workflow."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from eval_center.public_data import DATASET_VERSIONS, DEFAULT_DATA_ROOT, load_public_dataset, prepare_public_datasets
from eval_center.runtime import committed_code_sha, model_identities
from eval_center.verification import ExperimentInvalidError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reproducible public-dataset RAG benchmark workflow")
    parser.add_argument("--phase", choices=("preflight", "fetch", "prepare", "baseline", "optimize", "qa", "validate", "sync", "report", "paired-preflight", "paired-simulate", "paired-run", "paired-prepare-special"), required=True)
    parser.add_argument("--dataset", choices=("scifact", "miracl-zh", "longbench-zh"), default="scifact")
    parser.add_argument("--profile", choices=("smoke", "standard"), default="smoke")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--split", choices=("development", "locked_holdout"), default="development")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--chunk-overlap", type=int, default=120)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=32)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--context-budget-chars", type=int, default=8000)
    parser.add_argument("--cohort-file", type=Path,
                        default=Path("var/reports/v6-first-evaluation-20261010/cohort_dev24.jsonl"))
    parser.add_argument("--source-manifest", type=Path,
                        default=Path("var/reports/v6-first-evaluation-20261010/source_manifest.json"))
    parser.add_argument("--run-id")
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--index-manifest", type=Path)
    parser.add_argument("--runtime-entry", type=Path,
                        help="Explicit owner-reviewed local composition module; no automatic settings/key lookup")
    parser.add_argument("--runtime-entry-sha256", help="Required SHA256 of the reviewed composition module")
    parser.add_argument('--paired-time-profile',choices=('legacy-dev24','owner-small','owner-40q-24page',
        'owner-lease-small','owner-lease-40q-24page'),default='legacy-dev24')
    parser.add_argument('--paired-lease-id',help='New explicit execution lease identity; never renew an existing Run')
    parser.add_argument('--paired-lease-not-before-utc',help='UTC start from the reviewed native activity lease')
    parser.add_argument('--paired-lease-deadline-utc',help='Finite UTC native execution lease deadline, not an Owner total-task cutoff')
    parser.add_argument('--special-freeze',type=Path)
    parser.add_argument('--special-freeze-sha256')
    parser.add_argument('--special-data-root',type=Path)
    parser.add_argument('--special-output',type=Path)
    return parser


def _admin_url() -> str:
    from sqlalchemy.engine import make_url
    from eval_center.isolated_index import guarded_database_url
    value = os.environ.get("RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL")
    if not value:
        raise ExperimentInvalidError("missing_local_admin_database_url")
    parsed = make_url(value)
    if parsed.database != "postgres":
        raise ExperimentInvalidError("admin_database_must_be_postgres")
    guarded_database_url(parsed.set(database="rag_eval_trust_preflight"))
    return value


def _verify_sources(dataset) -> dict[str, int]:
    files = dataset.manifest.get("source_files", [])
    if not files:
        raise ExperimentInvalidError("source_file_manifest_missing")
    verified = 0
    total_bytes = 0
    for record in files:
        path = Path(record["path"])
        if not path.is_file():
            raise ExperimentInvalidError("public_source_cache_missing")
        payload = path.read_bytes()
        from hashlib import sha256
        if len(payload) != record["bytes"] or sha256(payload).hexdigest() != record["sha256"]:
            raise ExperimentInvalidError("public_source_cache_hash_mismatch")
        verified += 1
        total_bytes += len(payload)
    return {"files": verified, "bytes": total_bytes}


def _preflight(args) -> dict:
    dataset = load_public_dataset(args.dataset, args.data_root, split="development", phase="baseline")
    locked = load_public_dataset(args.dataset, args.data_root, split="locked_holdout", phase="validate")
    source_cache = _verify_sources(dataset)
    free_bytes = shutil.disk_usage(args.data_root).free
    result = {"dataset": args.dataset, "dataset_version": dataset.dataset_version,
              "development_documents": len(dataset.documents), "development_cases": len(dataset.cases),
              "locked_cases": len(locked.cases), "source_cache": source_cache,
              "free_bytes_at_data_root": free_bytes, "dry_run": bool(args.dry_run)}
    if args.dry_run:
        result.update(status="PREFLIGHT_DRY_RUN", models="NOT_CHECKED", postgres="NOT_CHECKED")
        return result
    from backend.app.adapters.models.ollama import OllamaGateway
    from backend.app.config import Settings
    settings = Settings.from_env()
    from sqlalchemy import create_engine, text
    result["models"] = model_identities(OllamaGateway(settings.ollama_base_url, settings.ollama_chat_model,
                                                       settings.ollama_embedding_model))
    admin_url = _admin_url()
    engine = create_engine(admin_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            database = connection.execute(text("SELECT current_database()")).scalar_one()
            vector_version = connection.execute(text("SELECT default_version FROM pg_available_extensions WHERE name='vector'")).scalar_one_or_none()
            if database != "postgres" or vector_version is None:
                raise ExperimentInvalidError("local_postgres_vector_unavailable")
            result["postgres"] = {"status": "ready", "vector_extension": vector_version}
    finally:
        engine.dispose()
    result["status"] = "PREFLIGHT_PASS"
    return result


def _paired_time_profile(args, composition=None):
    from eval_center.public_paired_reporting import time_profile
    profile=time_profile(args.paired_time_profile,
        lease_id=getattr(args,'paired_lease_id',None),
        lease_not_before_utc=getattr(args,'paired_lease_not_before_utc',None),
        lease_deadline_utc=getattr(args,'paired_lease_deadline_utc',None))
    if composition is not None:
        from eval_center.public_runner import bind_native_paired_time_profile
        profile=bind_native_paired_time_profile(profile,composition)
    return profile


def _load_runtime_composition(args, state_path):
    """Load only an explicitly selected, hash-frozen operator composition.

    The operator module defines compose_public_paired_runtime and must
    construct inputs, never dispatch providers.
    This is an evaluation CLI composition root, not a product Agent tool.
    """
    from eval_center.public_runner import PairedRuntimeAssemblyBlocked
    profile=_paired_time_profile(args)
    error=profile.availability_error(datetime.now(timezone.utc))
    if error:raise PairedRuntimeAssemblyBlocked([error])
    path = getattr(args, "runtime_entry", None)
    expected = getattr(args, "runtime_entry_sha256", None)
    if path is None or not Path(path).is_file():
        raise PairedRuntimeAssemblyBlocked(["reviewed_runtime_entry_missing"])
    data = Path(path).read_bytes()
    if not isinstance(expected, str) or hashlib.sha256(data).hexdigest() != expected:
        raise PairedRuntimeAssemblyBlocked(["reviewed_runtime_entry_hash_mismatch"])
    # Execute the exact verified bytes once, with no path reopen TOCTOU.
    namespace = {"__name__": "_owner_public_paired_composition", "__file__": str(Path(path).resolve())}
    exec(compile(data, str(path), "exec"), namespace)
    name = "compose_public_paired_runtime"
    factory = namespace.get(name)
    if not callable(factory):
        raise PairedRuntimeAssemblyBlocked([name + "_missing"])
    composition = factory(state_path=Path(state_path), index_manifest_path=args.index_manifest)
    from dataclasses import replace
    from eval_center.public_runner import PairedRuntimeComposition
    if type(composition) is not PairedRuntimeComposition:
        raise PairedRuntimeAssemblyBlocked(["typed_runtime_composition"])
    return replace(composition, entry_sha256=expected)


def _paired_phase(args, *, runtime=None, composition=None, simulate=False) -> dict:
    """Both CLI phases use the same admitted cohort runtime and scheduler."""
    from eval_center.public_runner import run_public_paired_cohort, build_normal_paired_runtime, PairedRuntimeAssemblyBlocked
    if simulate and runtime is not None and getattr(runtime, "execution_kind", None) != "SIMULATED":
        raise ExperimentInvalidError("paired_simulate_requires_simulated_runtime")
    state_path = args.state_path or Path("var/reports/v6-cohort-runtime-r1/cli-default/run_ledger.sqlite3")
    if getattr(args, "runtime_entry", None) is not None:
        if simulate or runtime is not None or composition is not None:
            raise ExperimentInvalidError("paired_runtime_entry_conflict")
        try:
            composition = _load_runtime_composition(args, state_path)
        except PairedRuntimeAssemblyBlocked as exc:
            return {"status": "BLOCKED_ZERO_SEND", "missing": exc.missing,
                    "provider_calls": 0, "send_attempts": 0, "quickchain_invocations": 0,
                    "database_queries": 0, "assembly": "NOT_STARTED"}
    if composition is not None and (runtime is not None or simulate):
        raise ExperimentInvalidError("paired_runtime_composition_conflict")
    if simulate and runtime is None:
        from eval_center.paired_simulation import build_simulated_runtime
        from eval_center.paired_state import load_frozen_cohort
        names=tuple(dict.fromkeys(row['dataset'] for row in load_frozen_cohort(args.cohort_file)))
        runtime_factory = lambda path: build_simulated_runtime(state_path=path, run_id=args.run_id,dataset_names=names)
    elif composition is not None:
        runtime_factory = lambda path: build_normal_paired_runtime(composition,
            source_manifest_path=args.source_manifest, index_manifest_path=args.index_manifest,
            cohort_path=args.cohort_file, run_id=args.run_id,
            time_profile=_paired_time_profile(args,composition))
    else:
        runtime_factory = lambda path: runtime
    try:
        return run_public_paired_cohort(
            cohort_path=args.cohort_file, source_path=args.source_manifest,
            data_root=args.data_root, state_path=state_path, run_id=args.run_id,
            runtime_factory=runtime_factory, index_manifest_path=args.index_manifest,
            top_k=args.top_k,
            time_profile=_paired_time_profile(args,composition),
        )
    except PairedRuntimeAssemblyBlocked as exc:
        return {"status": "BLOCKED_ZERO_SEND", "missing": exc.missing,
                "provider_calls": 0, "send_attempts": 0, "quickchain_invocations": 0,
                "database_queries": "UNKNOWN_READONLY_PREFLIGHT", "assembly": "PREFLIGHT_REJECTED"}


def _paired_simulate(args, *, runtime=None) -> dict:
    return _paired_phase(args, runtime=runtime, simulate=True)


def _paired_run(args, *, runtime=None, composition=None) -> dict:
    # Owner composition injects a fully wired PairedRuntime. Missing injection
    # is refused without Settings/key reads, engine creation or provider calls.
    return _paired_phase(args, runtime=runtime, composition=composition)


def _run(args, *, runtime=None, composition=None) -> dict:
    if args.phase=='paired-prepare-special':
        from eval_center.special_public_data import prepare_full_cohort
        if args.special_freeze is None or args.special_freeze_sha256 is None or args.special_output is None:
            raise ExperimentInvalidError('explicit_special_freeze_hash_and_output_required')
        return prepare_full_cohort(base_cohort_path=args.cohort_file,base_source_path=args.source_manifest,
            freeze_path=args.special_freeze,freeze_sha256=args.special_freeze_sha256,
            output_dir=args.special_output,asset_root=args.special_data_root)
    if args.phase == "paired-simulate":
        return _paired_simulate(args, runtime=runtime)
    if args.phase == "paired-run":
        return _paired_run(args, runtime=runtime, composition=composition)
    if args.phase == "paired-preflight":
        from eval_center.paired_state import load_frozen_cohort, sha256_file
        cases = load_frozen_cohort(args.cohort_file)
        source_manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
        if source_manifest.get("status") != "FROZEN_FOR_PREPARATION":
            raise ExperimentInvalidError("paired_source_manifest_not_frozen")
        counts = {name: sum(case["dataset"] == name for case in cases)
                  for name in ("scifact", "miracl-zh", "longbench-zh")}
        return {"status": "OFFLINE_PAIRED_COHORT_VERIFIED",
                "dispatch": "NOT_RUN", "database": "NOT_TOUCHED",
                "provider_calls": 0, "langfuse": "NOT_TOUCHED",
                "cohort_rows": len(cases), "dataset_counts": counts,
                "cohort_sha256": sha256_file(args.cohort_file),
                "source_manifest_sha256": sha256_file(args.source_manifest)}
    if args.phase == "preflight":
        return _preflight(args)
    if args.dry_run:
        return {"status": "DRY_RUN", "phase": args.phase, "dataset": args.dataset,
                "profile": args.profile, "split": args.split, "network_or_database_access": False,
                "data_root": str(args.data_root)}
    if args.phase == "fetch":
        prepared = prepare_public_datasets(args.data_root)
        dataset = load_public_dataset(args.dataset, args.data_root, phase="baseline")
        cache = _verify_sources(dataset)
        return {"status": "PINNED_SOURCE_CACHE_VERIFIED", "dataset": args.dataset,
                "network_downloads": 0, "source_cache": cache, "prepared_adapter": str(prepared[args.dataset])}
    if args.phase == "prepare":
        prepared = prepare_public_datasets(args.data_root)
        dataset = load_public_dataset(args.dataset, args.data_root, phase="baseline")
        return {"status": "PUBLIC_ADAPTER_PREPARED", "dataset": args.dataset,
                "documents": len(dataset.documents), "development_cases": len(dataset.cases),
                "path": str(prepared[args.dataset])}
    if args.phase in {"baseline", "validate", "optimize", "qa"}:
        from eval_center.public_runner import run_public_retrieval
        retrieval_variants = None
        generation_limit = 0
        split = "locked_holdout" if args.phase == "validate" else "development"
        phase = args.phase
        run_chunk_size = args.chunk_size
        run_chunk_overlap = args.chunk_overlap
        run_top_k, run_candidate_k, run_rrf_k = args.top_k, args.candidate_k, args.rrf_k
        run_context_budget = args.context_budget_chars
        if args.phase == "optimize":
            retrieval_variants = [
                {"variant_id": "baseline-t5-c32-r60", "top_k": 5, "candidate_k": 32, "rrf_k": 60},
                {"variant_id": "top-k-3", "top_k": 3, "candidate_k": 32, "rrf_k": 60},
                {"variant_id": "top-k-8", "top_k": 8, "candidate_k": 32, "rrf_k": 60},
                {"variant_id": "top-k-10", "top_k": 10, "candidate_k": 32, "rrf_k": 60},
                {"variant_id": "candidate-k-16", "top_k": 5, "candidate_k": 16, "rrf_k": 60},
                {"variant_id": "candidate-k-64", "top_k": 5, "candidate_k": 64, "rrf_k": 60},
                {"variant_id": "rrf-30", "top_k": 5, "candidate_k": 32, "rrf_k": 30},
            ]
        elif args.phase == "qa":
            if args.dataset != "longbench-zh":
                raise ExperimentInvalidError("qwen_qa_requires_longbench_zh")
            generation_limit = min(4, args.max_cases or 4)
        elif args.phase == "validate":
            if args.profile != "standard":
                raise ExperimentInvalidError("validation_requires_standard_profile")
            if args.max_cases is not None:
                raise ExperimentInvalidError("validation_must_score_full_locked_split")
            candidate_files = sorted(
                (Path(args.data_root) / "runs" / "candidates" / args.dataset).glob("*.json"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
            candidate_path = next((path for path in candidate_files
                                   if json.loads(path.read_text(encoding="utf-8")).get("candidate_status")
                                   == "opt_in_pending_locked_validation"), None)
            if candidate_path is None:
                raise ExperimentInvalidError("no_pending_development_candidate")
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            if candidate.get("dataset_version") != DATASET_VERSIONS[args.dataset] or candidate.get("split") != "development":
                raise ExperimentInvalidError("candidate_dataset_mismatch")
            source_run = Path(candidate["source_run"])
            from eval_center.public_selection import _digest
            if _digest(source_run / "manifest.json") != candidate.get("source_run_manifest_sha256"):
                raise ExperimentInvalidError("candidate_source_run_changed")
            selected = candidate["candidate_config"]
            run_chunk_size = selected["chunk_size"]
            run_chunk_overlap = selected["chunk_overlap"]
            run_top_k, run_candidate_k, run_rrf_k = selected["top_k"], selected["candidate_k"], selected["rrf_k"]
            run_context_budget = selected["context_budget_chars"]
            retrieval_variants = [{"variant_id": candidate["candidate_variant"],
                                   "top_k": selected["top_k"], "candidate_k": selected["candidate_k"],
                                   "rrf_k": selected["rrf_k"]}]
        result = run_public_retrieval(
            repository_root=Path(__file__).resolve().parents[1], data_root=args.data_root,
            dataset_name=args.dataset, admin_url=_admin_url(), split=split, phase=phase,
            profile=args.profile, chunk_size=run_chunk_size, chunk_overlap=run_chunk_overlap,
            top_k=run_top_k, candidate_k=run_candidate_k, rrf_k=run_rrf_k,
            context_budget_chars=run_context_budget, max_cases=args.max_cases,
            generation_limit=generation_limit, retrieval_variants=retrieval_variants,
        )
        if args.phase == "optimize" and args.profile == "standard":
            from eval_center.public_selection import select_development_candidate
            result["candidate_selection"] = select_development_candidate(Path(result["run_dir"]), args.data_root)
        if args.phase == "validate":
            candidate["candidate_status"] = "locked_validation_completed"
            candidate["locked_validation"] = {"run_dir": result["run_dir"], "git_sha": result["git_sha"],
                                                "completed_at": datetime.now(timezone.utc).isoformat()}
            candidate_path.write_text(json.dumps(candidate, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                                      encoding="utf-8", newline="\n")
        return result
    if args.phase == "report":
        from eval_center.public_report import write_public_report
        paths = write_public_report(args.data_root, Path(__file__).resolve().parents[1])
        counts = {}
        for dataset_name in ("scifact", "miracl-zh", "longbench-zh"):
            base = Path(args.data_root) / "runs" / dataset_name
            counts[dataset_name] = len(list(base.glob("**/manifest.json"))) if base.exists() else 0
        return {"status": "PUBLIC_REPORT_WRITTEN", **paths, "run_artifacts": counts}
    if args.phase == "sync":
        data_root = Path(args.data_root)
        candidate_dir = data_root / "runs" / "candidates" / args.dataset
        candidate_files = sorted(candidate_dir.glob("*.json"), key=lambda path: path.stat().st_mtime_ns,
                                 reverse=True) if candidate_dir.exists() else []
        candidate_path = next((path for path in candidate_files
                               if json.loads(path.read_text(encoding="utf-8")).get("candidate_status")
                               == "locked_validation_completed"), None)
        if candidate_path is None:
            raise ExperimentInvalidError("no_locked_validation_candidate")
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        from eval_center.public_selection import _digest
        source_run = Path(candidate.get("source_run", "")).resolve()
        expected_source_root = (data_root / "runs" / args.dataset / "development").resolve()
        if not source_run.is_relative_to(expected_source_root):
            raise ExperimentInvalidError("candidate_source_run_outside_data_root")
        source_manifest = source_run / "manifest.json"
        if (not source_manifest.is_file()
                or _digest(source_manifest) != candidate.get("source_run_manifest_sha256")):
            raise ExperimentInvalidError("candidate_source_run_changed")
        source_run_manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
        if (source_run_manifest.get("dataset") != args.dataset
                or source_run_manifest.get("profile") != "standard"
                or source_run_manifest.get("split") != "development"
                or source_run_manifest.get("git_sha") != candidate.get("git_sha")):
            raise ExperimentInvalidError("candidate_source_provenance_mismatch")
        validation = candidate.get("locked_validation")
        if not isinstance(validation, dict) or not isinstance(validation.get("run_dir"), str):
            raise ExperimentInvalidError("locked_validation_artifact_missing")
        run_dir = Path(validation["run_dir"]).resolve()
        expected_root = (data_root / "runs" / args.dataset / "locked_holdout").resolve()
        if not run_dir.is_relative_to(expected_root):
            raise ExperimentInvalidError("locked_validation_artifact_outside_data_root")
        run_manifest_path = run_dir / "manifest.json"
        if not run_manifest_path.is_file():
            raise ExperimentInvalidError("locked_validation_manifest_missing")
        run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        if (run_manifest.get("dataset") != args.dataset or run_manifest.get("split") != "locked_holdout"
                or run_manifest.get("profile") != "standard" or run_manifest.get("phase") != "validate"
                or run_manifest.get("git_sha") != candidate.get("git_sha")
                or run_manifest.get("dataset_version") != DATASET_VERSIONS[args.dataset]):
            raise ExperimentInvalidError("locked_validation_provenance_mismatch")
        lock_path = data_root / "runs" / "locks" / f"{args.dataset}-locked-holdout.json"
        if not lock_path.is_file():
            raise ExperimentInvalidError("locked_validation_lock_missing")
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if (lock.get("status") != "completed" or Path(lock.get("run_dir", "")).resolve() != run_dir
                or lock.get("git_sha") != run_manifest.get("git_sha")
                or _digest(run_manifest_path) != lock.get("run_manifest_sha256")
                or run_manifest.get("effective_config") != candidate.get("candidate_config")
                or run_manifest.get("effective_configs", {}).get(candidate.get("candidate_variant"))
                != candidate.get("candidate_config")):
            raise ExperimentInvalidError("locked_validation_lock_mismatch")
        from eval_center.public_sync import build_public_v2_bundle
        from eval_center.contracts import bundle_digest, normalize_bundle
        bundle, bundle_path = build_public_v2_bundle(run_dir, candidate["candidate_variant"])
        validated = normalize_bundle(bundle)
        digest = bundle_digest(validated)
        sync_status = {
            "status": "SANITIZED_BUNDLE_READY",
            "remote_transfer": "NOT_RUN_REMOTE_SHA_CHECK_PENDING",
            "dataset": args.dataset,
            "experiment_id": validated["manifest"]["experiment_id"],
            "git_sha": validated["manifest"]["git_sha"],
            "bundle_sha256": digest,
            "sample_count": validated["manifest"]["sample_count"],
            "bundle_path": str(bundle_path),
        }
        status_path = data_root / "reports" / f"sync-status-{args.dataset}.json"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(sync_status, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                               encoding="utf-8", newline="\n")
        return sync_status
    raise ExperimentInvalidError("unknown_public_benchmark_phase")


def main(argv: list[str] | None = None, *, runtime=None, composition=None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _run(args, runtime=runtime, composition=composition)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        print(json.dumps({"status": "FAIL", "error_code": code}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

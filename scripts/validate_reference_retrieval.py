"""One bounded, read-only comparison; never ingest, generate or resume a grid."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from backend.app.adapters.models.ollama import OllamaGateway
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.retrieval import HybridRetriever
from backend.app.application.retrieval_policy import RetrievalRouter
from backend.app.application.retrieval_profile import reference_profile
from backend.app.config import Settings
from eval_center.query_embedding_cache import EmbeddingCacheIdentity, RunScopedQueryEmbeddingCache
from eval_center.retrieval_replay import (
    BASELINE_ARM, _aggregate_retrieval, _context_for_result, _retrieval_arm,
    _verify_postflight, attach_verified_development_index,
)
from eval_center.runtime import committed_code_sha, model_identities
from eval_center.verified_index import IndexReuseRejected, inspect_known_containers, validate_clone_runtime

ROOT = Path(__file__).resolve().parents[1]
ARMS = ("Vector", "ReferenceHybrid", "Adaptive", "OldHybrid")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def run(data_root: Path, output: Path) -> dict:
    sha = committed_code_sha(ROOT)
    fixture_path = ROOT / "evaluations/reference_retrieval_qids.json"
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    original_bytes = (ROOT / fixture["source"]["original_fixture_path"]).read_bytes()
    if digest(original_bytes) != fixture["source"]["original_fixture_sha256"]:
        raise IndexReuseRejected("original_qid_fixture")
    original = json.loads(original_bytes)
    old_bytes = Path(fixture["source"]["replay_artifact_path"]).read_bytes()
    if digest(old_bytes) != fixture["source"]["source_artifact_sha256"]:
        raise IndexReuseRejected("old_results")
    old = json.loads(old_bytes)
    for entry in fixture["datasets"]:
        name, qids = entry["dataset_key"], entry["qids"]
        source = original["datasets"][name]["qids"]
        expected = sorted(source, key=lambda q: (digest(f"v1-reference-sanity-20260930|{name}|{q}".encode()), q))[:8]
        if qids != expected or len(set(qids)) != 8 or digest("\n".join(qids).encode()) != entry["selection_sha256"]:
            raise IndexReuseRejected("fixed_selection")
    if len(fixture["datasets"]) != 3 or fixture["total_qid_count"] != 24:
        raise IndexReuseRejected("call_budget")
    admin = os.environ.get("RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL")
    if not admin:
        raise IndexReuseRejected("credential_variable_missing")
    settings = Settings(cloud_enabled=False, langfuse_enabled=False)
    gateway = OllamaGateway(settings.ollama_base_url, settings.ollama_chat_model, settings.ollama_embedding_model)
    runtime = inspect_known_containers()
    runtime_proof = validate_clone_runtime(runtime)
    models = model_identities(gateway)
    attached = {}
    started = time.perf_counter()
    try:
        for entry in fixture["datasets"]:
            name = entry["dataset_key"]
            attached[name] = attach_verified_development_index(repository_root=ROOT, data_root=data_root,
                dataset_name=name, admin_database_url=admin, runtime=runtime, ollama=gateway)
            source = original["datasets"][name]
            item = attached[name]
            for field, actual in (("corpus_sha256", item.dataset.corpus_sha256),
                                  ("development_cases_sha256", item.dataset.development_cases_sha256),
                                  ("prepared_manifest_sha256", item.dataset.manifest_sha256),
                                  ("baseline_run_manifest_sha256", item.run_manifest_sha256)):
                if source[field] != actual:
                    raise IndexReuseRejected(field)
            if item.model_identities != models:
                raise IndexReuseRejected("cross_dataset_models")
        output.mkdir(parents=True, exist_ok=False)
        profile = reference_profile()
        params = profile["parameters"]
        prereg = {"schema_version": "reference-sanity-prereg-v1", "git_sha": sha,
            "fixture_sha256": digest(fixture_bytes), "profile": profile, "models": models,
            "indexes": {name: item.identity for name, item in attached.items()},
            "maximum_query_embedding_calls": 24, "maximum_retrieval_calls": 48,
            "arms": {"Vector": "new vector only", "ReferenceHybrid": "new fixed reference profile",
                     "Adaptive": "offline deterministic selection of the same two arms",
                     "OldHybrid": "saved original Round 1, no execution"},
            "gate": {"adaptive_max_harm_qids_per_dataset": 0,
                     "adaptive_min_mean_recall_difference": 0,
                     "identity_and_context_validation_required": True,
                     "gate_fail_action": "freeze Vector default; retain opt-in Hybrid; no new runs"},
            "command": [sys.executable, "-m", "scripts.validate_reference_retrieval", "--output", str(output)],
            "stop_rule": "single attempt; any degradation/identity mismatch stops; no retries or resume"}
        save(output / "manifest.json", prereg)
        completed = {}
        calls = 0
        for entry in fixture["datasets"]:
            name, qids = entry["dataset_key"], entry["qids"]
            item = attached[name]
            cache = RunScopedQueryEmbeddingCache(gateway, EmbeddingCacheIdentity(models["embedding"]["digest"], item.profile_id, 1024))
            retrievers = {arm: HybridRetriever(item.repository, embedding_provider=cache,
                mode="vector" if arm == "Vector" else "hybrid", top_k=params["top_k"],
                candidate_k=params["candidate_k"], rrf_k=params["rrf_k"], source_weights=params["source_weights"])
                for arm in ARMS[:2]}
            cases = {case.qid: case for case in item.dataset.cases}
            old_rows = {row["qid"]: row for row in old["datasets"][name]["per_qid"]}
            rows = []
            builder = ContextBuilder(params["context_max_chars"])
            for qid in qids:
                case = cases[qid]
                row = {"qid": qid, "question_sha256": digest(case.question.encode()),
                       "OldHybrid": old_rows[qid][BASELINE_ARM]}
                order = ARMS[:2] if int(digest(qid.encode())[:2], 16) % 2 == 0 else ARMS[1::-1]
                ranks = []
                for arm in order:
                    result, latency = _retrieval_arm(retrievers[arm], case, item.kb_id)
                    calls += 1
                    ranks.append([hit.chunk_id for hit in result.candidate_rankings["vector"]])
                    context_started = time.perf_counter()
                    stats = _context_for_result(result=result, repository=item.repository, case=case,
                        index=item.index, context_builder=builder, arm=BASELINE_ARM, top_k=params["top_k"])
                    stats.update(context_building_ms=(time.perf_counter()-context_started)*1000,
                        retrieval_latency_ms=latency, retrieval_mode=result.retrieval_mode,
                        route_reason=list(result.route_reason), stage_latency_ms=result.stage_latency_ms,
                        vector_candidate_count=len(result.candidate_rankings.get("vector", ())),
                        keyword_candidate_count=len(result.candidate_rankings.get("keyword", ())),
                        retrieved_chunk_ids=[hit.chunk.chunk_id for hit in result.items],
                        selected_chunk_ids=[hit.chunk.chunk_id for hit in builder.select(result.items)],
                        context_tokens="NOT_AVAILABLE", llm_provider="none", llm_model="none",
                        input_tokens="NOT_AVAILABLE", output_tokens="NOT_AVAILABLE", total_tokens="NOT_AVAILABLE",
                        cloud_called=False, error=None, retry_count=0)
                    row[arm] = stats
                    save(output / f"checkpoint-{name}-{digest(qid.encode())[:16]}-{arm}.json", stats)
                if ranks[0] != ranks[1]:
                    raise IndexReuseRejected("vector_ranking_stability")
                route = RetrievalRouter("adaptive").route(case.question)
                selected_arm = "ReferenceHybrid" if route.mode == "hybrid" else "Vector"
                row["Adaptive"] = {**row[selected_arm], "route_reason": list(route.reason), "selected_arm": selected_arm}
                rows.append(row)
                print(json.dumps({"dataset": name, "completed": len(rows), "total": 8, "retrieval_calls": calls}), flush=True)
                save(output / f"results-{name}.json", rows)
            summary = {arm: _aggregate_retrieval(rows, arm) for arm in ARMS}
            for arm in ARMS[1:]:
                summary[arm]["rescue_vs_vector"] = sum(row["Vector"]["document_metrics_at_5"]["hit_at_k"] == 0 and row[arm]["document_metrics_at_5"]["hit_at_k"] == 1 for row in rows)
                summary[arm]["harm_vs_vector"] = sum(row["Vector"]["document_metrics_at_5"]["hit_at_k"] == 1 and row[arm]["document_metrics_at_5"]["hit_at_k"] == 0 for row in rows)
            completed[name] = {"per_qid": rows, "summary": summary, "cache": cache.snapshot()}
        postflight = _verify_postflight(attached, gateway, models, runtime_proof)
        passed = all(value["summary"]["Adaptive"]["harm_vs_vector"] == 0 and
            value["summary"]["Adaptive"]["document_recall_at_5"] >= value["summary"]["Vector"]["document_recall_at_5"]
            for value in completed.values())
        result = {"status": "COMPLETED", "gate": "PASS" if passed else "FAIL", "git_sha": sha,
            "preregistration_sha256": digest((output/"manifest.json").read_bytes()), "datasets": completed,
            "retrieval_calls": calls, "query_embedding_calls": sum(v["cache"]["provider_calls"] for v in completed.values()),
            "corpus_embedding_calls": 0, "database_writes": 0, "llm_calls": 0, "locked_calls": 0,
            "postflight": postflight, "elapsed_seconds": time.perf_counter()-started, "exit_code": 0}
        save(output / "results.json", result)
        return {key: value for key, value in result.items() if key != "datasets"}
    finally:
        for item in attached.values():
            item.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path(r"D:\RAG-Public-Bench"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.data_root, args.output), ensure_ascii=False))
        return 0
    except Exception as exc:
        # Credentials and raw questions never enter console/error reports.
        state = {"status": "INTERRUPTED", "error_type": type(exc).__name__, "field": getattr(exc, "field", None), "exit_code": 2}
        if args.output.exists():
            save(args.output / "stop.json", state)
        print(json.dumps(state))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

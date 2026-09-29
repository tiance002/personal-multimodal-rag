import hashlib
import json
from types import SimpleNamespace

import pytest

from backend.app.domain.models import RankedHit
from eval_center.public_m3_abc import (
    BASE_CONFIG,
    MODES,
    M0_DIAGNOSTIC_QIDS,
    SAMPLE_SIZES,
    SEED,
    freeze_preregistration,
    select_qids,
    summarize_results,
    validate_retrieval_result,
    verify_frozen_sample,
)
from eval_center.public_data import DATASET_VERSIONS
from eval_center.verification import ExperimentInvalidError


class Retriever:
    def __init__(self, mode):
        spec = MODES[mode]
        self.enabled_sources = spec["enabled_sources"]
        self.source_weights = spec["source_weights"]

    def effective_config(self):
        return {key: BASE_CONFIG[key] for key in ("top_k", "candidate_k", "rrf_k")}


def result(*, sources=("vector",), vector=(RankedHit(chunk_id="v1", rank=1),),
           fused=(RankedHit(chunk_id="v1", rank=1),), degraded=(), config=None):
    return SimpleNamespace(
        sources=sources,
        candidate_rankings={"vector": vector},
        fused_ranking=fused,
        degradation_flags=degraded,
        effective_config=config or {key: BASE_CONFIG[key] for key in ("top_k", "candidate_k", "rrf_k")},
    )


def test_sample_is_hash_ordered_over_development_ids_only_and_excludes_prior_ids():
    qids = [f"qid-{number}" for number in range(100)]
    qids.extend(("old-m1", "old-m0"))
    selected, hashes = select_qids(qids, {"old-m1", "old-m0"}, dataset="scifact", size=20)
    expected = sorted(
        set(qids) - {"old-m1", "old-m0"},
        key=lambda qid: (hashlib.sha256(
            f"scifact|{qid}|{SEED}".encode()).hexdigest(), qid),
    )[:20]
    assert selected == expected
    assert len(set(selected)) == 20
    assert set(selected).isdisjoint({"old-m1", "old-m0"})
    assert set(hashes) == set(selected)


@pytest.mark.parametrize("dataset,size", SAMPLE_SIZES.items())
def test_preregistered_sample_sizes_are_exact(dataset, size):
    qids = [f"q-{number}" for number in range(size + 5)]
    selected, _ = select_qids(qids, set(qids[-5:]), dataset=dataset, size=size)
    assert len(selected) == size


def test_vector_only_scoring_uses_real_vector_candidate_list():
    vector = (RankedHit(chunk_id="vector-hit", rank=1),)
    fused = (RankedHit(chunk_id="keyword-borrowed", rank=1),)
    actual = result(vector=vector, fused=fused)
    assert validate_retrieval_result(Retriever("A-vector-only"), actual,
                                     "A-vector-only") is vector


@pytest.mark.parametrize(
    "actual,mode,code",
    [
        (result(sources=("keyword",)), "A-vector-only", "retrieval_source_set_mismatch"),
        (result(degraded=("VECTOR_UNAVAILABLE",)), "A-vector-only", "retrieval_degraded"),
        (result(vector=()), "A-vector-only", "vector_candidates_missing"),
        (result(config={"top_k": 5, "candidate_k": 32, "rrf_k": 61}),
         "A-vector-only", "retrieval_config_mismatch"),
    ],
)
def test_vector_gate_fails_closed_before_metrics(actual, mode, code):
    with pytest.raises(ExperimentInvalidError) as error:
        validate_retrieval_result(Retriever(mode), actual, mode)
    assert error.value.code == code


def test_hybrid_modes_require_both_real_source_rankings_and_fusion():
    retriever = Retriever("B-hybrid-rrf")
    actual = result(sources=("keyword", "vector"), fused=())
    with pytest.raises(ExperimentInvalidError, match="fused_candidates_missing"):
        validate_retrieval_result(retriever, actual, "B-hybrid-rrf")


def test_weighted_mode_configuration_is_fixed_before_scoring():
    retriever = Retriever("C-vector-priority-0.8-0.2")
    retriever.source_weights = {"keyword": 0.8, "vector": 0.2}
    with pytest.raises(ExperimentInvalidError) as error:
        validate_retrieval_result(retriever, result(sources=("keyword", "vector")),
                                  "C-vector-priority-0.8-0.2")
    assert error.value.code == "retriever_weight_config_mismatch"


def test_freeze_writes_qids_only_and_verifies_the_preregistered_hash(tmp_path, monkeypatch):
    provenance = {"git_sha": "a" * 40, "tree_oid": "b" * 40,
                 "patch_base_sha": "c" * 40, "patch_sha256": "d" * 64,
                 "working_tree_clean": True}
    monkeypatch.setattr("eval_center.public_m3_abc.committed_source_provenance",
                        lambda _repository: provenance)
    data_root = tmp_path / "bench"
    previous_qids = {}
    for dataset in ("scifact", "miracl-zh", "longbench-zh"):
        directory = data_root / "prepared" / "adapters" / dataset
        (directory / "cases").mkdir(parents=True)
        qids = list(M0_DIAGNOSTIC_QIDS[dataset]) + [f"new-{i}" for i in range(80)]
        previous_qids[dataset] = list(M0_DIAGNOSTIC_QIDS[dataset])
        rows = [{"qid": qid, "split": "development"} for qid in qids]
        payload = b"".join((json.dumps(row, sort_keys=True) + "\n").encode() for row in rows)
        cases_path = directory / "cases" / "development.jsonl"
        cases_path.write_bytes(payload)
        manifest = {
            "dataset": dataset,
            "dataset_version": DATASET_VERSIONS[dataset],
            "files": [{"path": "cases/development.jsonl", "bytes": len(payload),
                       "sha256": hashlib.sha256(payload).hexdigest()}],
        }
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    history = tmp_path / "m1-m2.json"
    history.write_text(json.dumps({"status": "COMPLETED", "qid_sets": previous_qids}),
                       encoding="utf-8")
    output = tmp_path / "frozen"
    frozen = freeze_preregistration(data_root=data_root, m1_m2_manifest=history,
                                    output_dir=output)
    cases_path = output / "cases.jsonl"
    assert frozen["qid_sets"] == json.loads((output / "preregistration.json").read_text())["qid_sets"]
    case_rows = [json.loads(line) for line in cases_path.read_text().splitlines()]
    assert all(set(row) == {"dataset", "qid", "selection_sha256"} for row in case_rows)
    verified = verify_frozen_sample(output / "preregistration.json", cases_path, data_root,
                                    frozen["preregistration_sha256"])
    assert verified["qid_set_sha256"] == frozen["qid_set_sha256"]
    with pytest.raises(ExperimentInvalidError) as error:
        verify_frozen_sample(output / "preregistration.json", cases_path, data_root, "0" * 64)
    assert error.value.code == "preregistration_hash_mismatch"


def test_summary_keeps_dataset_metrics_and_paired_transitions_separate(tmp_path):
    rows = []
    for dataset in ("scifact", "miracl-zh", "longbench-zh"):
        for mode, recall, hits in (("A-vector-only", 0.25, ["d1"]),
                                   ("B-hybrid-rrf", 0.5, ["d1", "d2"]),
                                   ("C-vector-priority-0.8-0.2", 0.75, ["d2"])):
            rows.append({
                "status": "COMPLETE", "dataset": dataset, "qid": "q1", "mode": mode,
                "positive_document_hits_at_5": hits, "positive_context_hits": hits[:1],
                "judged_to_unjudged_ratio": 0.01,
                "metrics": {"ndcg_at_10": recall, "recall_at_5": recall,
                            "mrr_at_5": recall, "context_source_recall_at_5": recall,
                            "source_diversity": 2, "parent_diversity": 1,
                            "context_chars": 100, "retrieval_ms": 10,
                            "embedding_ms": 0 if mode != "A-vector-only" else 5},
                "cache_state": "WARM_CACHE_HIT" if mode != "A-vector-only" else "COLD_EMBEDDING",
            })
    results_path = tmp_path / "results.jsonl"
    results_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    summary = summarize_results(results_path)
    scifact = summary["datasets"]["scifact"]
    pair = scifact["paired_deltas"]["A-vector-only__vs__B-hybrid-rrf"]
    assert pair["n"] == 1
    assert pair["metrics"]["recall_at_5"]["mean_delta"] == pytest.approx(0.25)
    assert pair["metrics"]["recall_at_5"]["bootstrap_95_percentile_interval"] == [0.25, 0.25]
    transitions = scifact["positive_transitions"]["A-vector-only__vs__B-hybrid-rrf"]
    assert transitions["positive_document_hits_at_5"]["total_entered"] == 1
    assert transitions["positive_document_hits_at_5"]["total_exited"] == 0
    assert summary["datasets"]["miracl-zh"]["mode_metrics"]["A-vector-only"]["judged_to_unjudged_ratio"] == 0.01

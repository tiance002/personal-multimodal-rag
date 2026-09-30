from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

from eval_center.verified_index import (
    IndexReuseRejected,
    read_only_database_url,
    validate_clone_runtime,
    validate_index_identity,
)
from eval_center import retrieval_replay


def identity():
    return {
        "dataset": "scifact",
        "dataset_version": "scifact-v1",
        "split": "development",
        "run_id": "run-123",
        "run_manifest_sha256": "a" * 64,
        "corpus_sha256": "b" * 64,
        "development_cases_sha256": "c" * 64,
        "prepared_manifest_sha256": "d" * 64,
        "parser_version": "text/v1",
        "chunker_version": "adaptive/v1",
        "chunk_size": 1200,
        "chunk_overlap": 120,
        "embedding_model": "bge-m3:latest",
        "embedding_digest": "e" * 64,
        "embedding_profile": "profile-123",
        "embedding_dimension": 1024,
        "schema_revision": "0013_message_run_link",
        "ready_state": True,
        "index_fingerprint": "f" * 64,
        "index_counts": {"documents": 10, "chunks": 12, "embeddings": 12},
        "source_binding_sha256": "1" * 64,
        "index_code_sha256": "2" * 64,
        "baseline_git_sha": "3" * 40,
        "container_id": "4" * 64,
        "container_name": "rag-eval-trust0928-db-clone-20260929t091523z",
        "volume_name": "rag-eval-trust0928-db-clone-20260929t091523z",
        "database": "rag_eval_trust_6f0c61007b_scifact",
        "port": 25437,
        "database_read_only": True,
    }


def runtime(*, clone_volume=None, original_volume=None, clone_state="running", original_state="exited", host_ip="127.0.0.1", host_port=25437):
    return {
        "clone": {
            "id": "4" * 64,
            "name": "rag-eval-trust0928-db-clone-20260929t091523z",
            "state": clone_state,
            "volume": clone_volume or "rag-eval-trust0928-db-clone-20260929t091523z",
            "volume_destination": "/var/lib/postgresql/data",
            "host_ip": host_ip,
            "host_port": host_port,
        },
        "original": {
            "id": "5" * 64,
            "name": "rag-eval-trust0928-db",
            "state": original_state,
            "volume": original_volume or "c7f257b7cb2036ce42cb7c074d2aff92b5c32a551badf98c80589b1cc3ce9c53",
        },
    }


def test_complete_identical_index_identity_is_accepted_and_hashed():
    expected = identity()
    actual = deepcopy(expected)

    result = validate_index_identity(expected, actual)

    assert result["status"] == "VERIFIED_READ_ONLY_REUSE"
    assert result["identity_sha256"] == validate_index_identity(expected, actual)["identity_sha256"]
    assert result["verified_fields"] == sorted(expected)


@pytest.mark.parametrize(
    "field,value",
    [
        ("chunk_size", 800),
        ("embedding_digest", "9" * 64),
        ("schema_revision", "0012_chunk_strategy"),
        ("index_fingerprint", "8" * 64),
        ("ready_state", False),
        ("source_binding_sha256", "7" * 64),
        ("index_code_sha256", "6" * 64),
        ("database_read_only", False),
    ],
)
def test_any_identity_mismatch_fails_closed(field, value):
    expected = identity()
    actual = deepcopy(expected)
    actual[field] = value

    with pytest.raises(IndexReuseRejected, match="INDEX_REUSE_REJECTED"):
        validate_index_identity(expected, actual)


def test_missing_identity_field_fails_closed():
    expected = identity()
    actual = deepcopy(expected)
    actual.pop("index_fingerprint")

    with pytest.raises(IndexReuseRejected, match="INDEX_REUSE_REJECTED"):
        validate_index_identity(expected, actual)


def test_database_url_is_forced_to_loopback_clone_and_read_only():
    result = read_only_database_url(
        "postgresql+psycopg://user:secret@127.0.0.1:25437/postgres",
        "rag_eval_trust_6f0c61007b_scifact",
    )
    parsed = make_url(result)

    assert parsed.host == "127.0.0.1"
    assert parsed.port == 25437
    assert parsed.database == "rag_eval_trust_6f0c61007b_scifact"
    assert parsed.query["options"] == "-c default_transaction_read_only=on"
    assert "secret" not in parsed.render_as_string(hide_password=True)


def test_database_url_discards_driver_routing_overrides():
    result = read_only_database_url(
        "postgresql+psycopg://user:example@127.0.0.1:25437/postgres?host=example.invalid&hostaddr=203.0.113.5&port=5432&dbname=other",
        "rag_eval_trust_6f0c61007b_scifact",
    )
    parsed = make_url(result)
    _, driver_args = PGDialect_psycopg().create_connect_args(parsed)

    assert driver_args["host"] == "127.0.0.1"
    assert str(driver_args["port"]) == "25437"
    assert set(parsed.query) == {"options", "application_name"}


def test_frozen_qid_list_requires_its_canonical_hash_and_dataset_membership():
    qids = ["qid-a", "qid-b"]
    digest = hashlib.sha256(json.dumps(qids, separators=(",", ":")).encode()).hexdigest()

    assert retrieval_replay.validate_frozen_qid_list(qids, digest, {"qid-a", "qid-b"}, limit=2) == qids
    with pytest.raises(IndexReuseRejected, match="qid_list_sha256"):
        retrieval_replay.validate_frozen_qid_list(qids, "0" * 64, {"qid-a", "qid-b"}, limit=2)
    outside_qids = ["qid-a", "qid-c"]
    outside_digest = hashlib.sha256(json.dumps(outside_qids, separators=(",", ":")).encode()).hexdigest()
    with pytest.raises(IndexReuseRejected, match="frozen_development_qids"):
        retrieval_replay.validate_frozen_qid_list(outside_qids, outside_digest, {"qid-a", "qid-b"}, limit=2)


def test_preflight_only_attaches_all_indexes_without_starting_retrieval(tmp_path, monkeypatch):
    fixture_path = Path(__file__).parents[1] / "fixtures" / "rag-retrieval-round1-qids.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    model_identity = {"chat": {"name": "qwen3.5:4b", "digest": "a" * 64},
                      "embedding": {"name": "bge-m3:latest", "digest": "b" * 64}}
    attached = {}
    for name, entry in fixture["datasets"].items():
        attached[name] = SimpleNamespace(
            dataset=SimpleNamespace(name=name, corpus_sha256=entry["corpus_sha256"],
                                    development_cases_sha256=entry["development_cases_sha256"],
                                    manifest_sha256=entry["prepared_manifest_sha256"],
                                    cases=tuple(SimpleNamespace(qid=qid) for qid in entry["qids"])),
            run_manifest_sha256=entry["baseline_run_manifest_sha256"],
            model_identities=model_identity,
            identity_proof={"identity_sha256": "c" * 64, "status": "VERIFIED_READ_ONLY_REUSE"},
            dispose=lambda: None,
        )
    monkeypatch.setattr(retrieval_replay, "inspect_known_containers", lambda: {"verified": True})
    monkeypatch.setattr(retrieval_replay, "validate_clone_runtime", lambda _runtime: {"clone_container_id": "d" * 64})
    monkeypatch.setattr(retrieval_replay, "Settings", lambda **_kwargs: SimpleNamespace(
        ollama_base_url="http://127.0.0.1:11434", ollama_chat_model="qwen3.5:4b",
        ollama_embedding_model="bge-m3:latest"))
    monkeypatch.setattr(retrieval_replay, "OllamaGateway", lambda *_args: object())
    monkeypatch.setattr(retrieval_replay, "model_identities", lambda _ollama: model_identity)
    monkeypatch.setattr(retrieval_replay, "committed_code_sha", lambda _root: "e" * 40)
    monkeypatch.setattr(retrieval_replay, "attach_verified_development_index", lambda **kwargs: attached[kwargs["dataset_name"]])
    monkeypatch.setattr(retrieval_replay, "_run_frozen_retrieval", lambda *_args: pytest.fail("retrieval started during preflight"))

    result = retrieval_replay.run_frozen_development_replay(
        repository_root=tmp_path,
        data_root=tmp_path / "data",
        qid_fixture_path=fixture_path,
        admin_database_url="unused",
        output_root=tmp_path / "output",
        preflight_only=True,
    )

    assert result["status"] == "PREFLIGHT_COMPLETE"
    assert result["query_embedding_calls"] == 0
    assert len(result["indexes"]) == 3
    assert not (tmp_path / "output").exists()


def test_postflight_requires_unchanged_runtime_and_model_digests():
    models = {"embedding": {"digest": "a" * 64}, "chat": {"digest": "b" * 64}}
    runtime_proof = {"clone_container_id": "c" * 64, "clone_volume": "clone", "original_stopped": True}

    assert retrieval_replay.validate_postflight_identity(models, models, runtime_proof, deepcopy(runtime_proof))[
        "status"] == "IDENTITY_UNCHANGED"
    changed_models = deepcopy(models)
    changed_models["chat"]["digest"] = "d" * 64
    with pytest.raises(IndexReuseRejected, match="model_digest_postflight"):
        retrieval_replay.validate_postflight_identity(models, changed_models, runtime_proof, runtime_proof)
    changed_runtime = deepcopy(runtime_proof)
    changed_runtime["clone_container_id"] = "e" * 64
    with pytest.raises(IndexReuseRejected, match="runtime_postflight"):
        retrieval_replay.validate_postflight_identity(models, models, runtime_proof, changed_runtime)


@pytest.mark.parametrize(
    "url,database",
    [
        ("postgresql+psycopg://user:secret@example.com:25437/postgres", "rag_eval_trust_6f0c61007b_scifact"),
        ("postgresql+psycopg://user:secret@127.0.0.1:5432/postgres", "rag_eval_trust_6f0c61007b_scifact"),
        ("postgresql+psycopg://user:secret@127.0.0.1:25437/rag", "rag_eval_trust_6f0c61007b_scifact"),
        ("postgresql+psycopg://user:secret@127.0.0.1:25437/postgres", "rag_eval_trust_other"),
    ],
)
def test_database_url_rejects_nonclone_targets(url, database):
    with pytest.raises(IndexReuseRejected, match="INDEX_REUSE_REJECTED"):
        read_only_database_url(url, database)


def test_clone_mount_and_port_identity_is_verified():
    result = validate_clone_runtime(runtime())

    assert result["clone_container_id"] == "4" * 64
    assert result["database_port"] == 25437
    assert result["original_stopped"] is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"clone_volume": "c7f257b7cb2036ce42cb7c074d2aff92b5c32a551badf98c80589b1cc3ce9c53"},
        {"clone_state": "exited"},
        {"original_state": "running"},
        {"host_ip": "0.0.0.0"},
        {"host_port": 25436},
    ],
)
def test_unsafe_clone_runtime_identity_is_rejected(kwargs):
    with pytest.raises(IndexReuseRejected, match="INDEX_REUSE_REJECTED"):
        validate_clone_runtime(runtime(**kwargs))

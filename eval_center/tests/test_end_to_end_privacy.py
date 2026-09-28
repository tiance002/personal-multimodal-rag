from __future__ import annotations

import hashlib
import json
import threading
import urllib.request

from eval_center.cli import main
from eval_center.server import make_server
from scripts.package_eval_bundle import build_bundle


_CANARIES = (
    "CANARY_PRIVATE_QUESTION_73f8",
    "CANARY_PRIVATE_ANSWER_83a9",
    "CANARY_PRIVATE_CHUNK_94ba",
    "CANARY_PRIVATE_TRACEBACK_a5cb",
    "CANARY_PRIVATE_TOKEN_b6dc",
)


def _inputs(experiment_id: str, mrr: float):
    config = {"top_k": 5, "retrieval_mode": "hybrid"}
    report = {
        "status": "PASS",
        "mode": "deterministic_fixture",
        "dataset_version": "core-v1",
        "metrics": {"mrr": mrr, "hit_at_5": 1.0},
        "cases": [{
            "case_id": "core-001", "question": _CANARIES[0], "answer": _CANARIES[1],
            "retrieved_chunk_ids": [_CANARIES[2]], "traceback": _CANARIES[3],
            "api_token": _CANARIES[4], "pass": True, "mrr": mrr,
        }],
    }
    manifest = {
        "experiment_id": experiment_id,
        "git_sha": "a0a62e8ee78be4a176b81e3f61cf5b84dd6d364b",
        "dataset_version": "core-v1",
        "corpus_hash": "a" * 64,
        "model_profile": "q0-deterministic",
        "embedding_profile": "none",
        "started_at": "2026-09-27T10:00:00+08:00",
        "ended_at": "2026-09-27T10:00:03+08:00",
        "environment": {"os_family": "windows", "python_version": "3.12.5", "architecture": "x86_64"},
    }
    return report, manifest, config


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=3) as response:
        assert response.status == 200
        return response.read()


def test_sanitized_bundle_import_dashboard_and_database_never_expose_raw_canaries(tmp_path, monkeypatch, capsys):
    database = tmp_path / "registry.sqlite3"
    bundle_paths = []
    for experiment_id, mrr in (
        ("e4a3e2db-5773-4d12-9be9-2db322654e12", 1.0),
        ("d7fa2e01-472c-47c0-903b-0d496c4d8f3a", 0.5),
    ):
        report, manifest, config = _inputs(experiment_id, mrr)
        bundle = build_bundle(report, manifest, config)
        bundle_path = tmp_path / f"{experiment_id}.json"
        bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        serialized_bundle = bundle_path.read_text(encoding="utf-8")
        assert all(canary not in serialized_bundle for canary in _CANARIES)
        bundle_paths.append(bundle_path)

    monkeypatch.setenv("EVAL_CENTER_DB", str(database))
    for bundle_path in bundle_paths:
        assert main(["import", str(bundle_path)]) == 0
        assert all(canary not in capsys.readouterr().out for canary in _CANARIES)

    server = make_server(database, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        base = f"http://{host}:{port}"
        responses = [
            _get(f"{base}/api/v1/experiments?limit=10&offset=0&view=diagnostic"),
            _get(f"{base}/api/v1/experiments/e4a3e2db-5773-4d12-9be9-2db322654e12"),
            _get(f"{base}/api/v1/compare?ids=e4a3e2db-5773-4d12-9be9-2db322654e12,d7fa2e01-472c-47c0-903b-0d496c4d8f3a"),
        ]
        assert json.loads(responses[0])["experiments"]
        assert json.loads(responses[2])["comparable"] is False
        assert json.loads(responses[2])['metrics'] == {}
        assert json.loads(_get(f'{base}/api/v1/experiments'))['experiments'] == []
        assert all(canary.encode() not in response for response in responses for canary in _CANARIES)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    for database_artifact in database.parent.glob(database.name + "*"):
        if database_artifact.is_file():
            contents = database_artifact.read_bytes()
            assert all(canary.encode() not in contents for canary in _CANARIES)

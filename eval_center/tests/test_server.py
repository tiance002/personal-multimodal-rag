from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from eval_center.server import make_server
from eval_center.store import import_bundle, initialize_database
from eval_center.tests.test_store import make_bundle
from eval_center.tests.test_v2_import import make_v2


def request_json(url: str) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as response:
        return response.code, json.loads(response.read())


@pytest.fixture
def running_server(tmp_path):
    database = tmp_path / "runs.sqlite3"
    initialize_database(database)
    first_id = "e4a3e2db-5773-4d12-9be9-2db322654e12"
    second_id = "d7fa2e01-472c-47c0-903b-0d496c4d8f3a"
    first=make_v2()
    second=make_v2()
    first['manifest']['experiment_id']=first_id
    second['manifest'].update(experiment_id=second_id,dataset_version='core-v2')
    import_bundle(database,first)
    import_bundle(database,second)
    server = make_server(database, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_read_only_loopback_routes_health_list_detail_and_compare(running_server):
    base = running_server

    status, health = request_json(f"{base}/healthz")
    assert status == 200
    assert health == {"status": "ok", "git_sha": None}

    status, listing = request_json(f"{base}/api/v1/experiments?limit=10&offset=0")
    assert status == 200
    assert len(listing["experiments"]) == 2

    experiment_id = "e4a3e2db-5773-4d12-9be9-2db322654e12"
    status, detail = request_json(f"{base}/api/v1/experiments/{experiment_id}")
    assert status == 200
    assert detail["experiment_id"] == experiment_id

    ids = f"{experiment_id},d7fa2e01-472c-47c0-903b-0d496c4d8f3a"
    status, comparison = request_json(f"{base}/api/v1/compare?ids={ids}")
    assert status == 200
    assert comparison["comparable"] is False
    assert "dataset_version" in comparison["mismatches"]


@pytest.mark.parametrize("host", ["0.0.0.0", "8.8.8.8"])
def test_server_refuses_non_loopback_bind(host, tmp_path):
    with pytest.raises(ValueError):
        make_server(tmp_path / "runs.sqlite3", host=host, port=0)


def test_static_page_is_bundled_and_unknown_or_traversal_paths_are_not_served(running_server):
    base = running_server
    with urllib.request.urlopen(f"{base}/", timeout=3) as response:
        page = response.read().decode("utf-8")
        assert response.status == 200
        assert "RAG 评测中心" in page
        assert response.headers["Content-Security-Policy"]
        assert "textContent" in page
        assert "innerHTML" not in page

    assert request_json(f"{base}/../../../../etc/passwd")[0] == 404
    assert request_json(f"{base}/static/../../etc/passwd")[0] == 404
    assert request_json(f"{base}/unknown")[0] == 404


def test_write_methods_are_rejected_without_mutating_registry(running_server):
    request = urllib.request.Request(
        f"{running_server}/api/v1/experiments",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=3)
    assert exc.value.code == 405
    status, listing = request_json(f"{running_server}/api/v1/experiments")
    assert status == 200
    assert len(listing["experiments"]) == 2

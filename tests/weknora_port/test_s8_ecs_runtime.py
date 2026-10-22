"""Offline Linux deployment contracts; fake ASGI only, no resources or model sends."""
import asyncio
from pathlib import Path
import hashlib
import json
import tomllib

import httpx
import pytest
import yaml

from scripts.s8_ecs_runtime import (
    PrivateApiGuard, RuntimeConfigurationError, api_token, runtime_environment, trusted_principal,
)

ROOT = Path(__file__).resolve().parents[2]


def base_env(tmp_path):
    password = tmp_path / "database_password"
    password.write_text("synthetic@password:only", encoding="utf8")
    return {"S8_DB_PASSWORD_FILE": str(password), "WEKNORA_PORT_DATABASE_NAME": "weknora_s8",
            "WEKNORA_PORT_DATABASE_SYSTEM_ID": "123456", "WEKNORA_PORT_REDIS_RUN_ID": "a" * 40,
            "WEKNORA_PORT_REDIS_PREFIX": "weknora-python-s8:runtime"}


@pytest.mark.parametrize('configured,expected',[(None,'true'),('true','true'),('false','false')])
def test_owner_top1_default_and_explicit_server_override(tmp_path,configured,expected):
    environment=base_env(tmp_path)
    if configured is not None:environment['WEKNORA_PORT_RERANK_TOP1_FLOORLESS']=configured
    actual=runtime_environment(environment)
    assert actual['WEKNORA_PORT_RERANK_TOP1_FLOORLESS']==expected
    assert actual['WEKNORA_PORT_GRAPH_ENABLED']=='false'
    assert actual['WEKNORA_PORT_CLOUD_ALLOWED']=='false'


@pytest.mark.parametrize('value',['','1','yes','not-a-boolean'])
def test_owner_top1_environment_rejects_nonboolean_values(tmp_path,value):
    with pytest.raises(RuntimeConfigurationError,match='RERANK_TOP1_SETTING_INVALID'):
        runtime_environment({**base_env(tmp_path),'WEKNORA_PORT_RERANK_TOP1_FLOORLESS':value})


def test_runtime_defaults_closed_and_original_strategy_without_secret_logging(tmp_path):
    env = runtime_environment(base_env(tmp_path))
    assert env["WEKNORA_PORT_CLOUD_ALLOWED"] == "false"
    assert env["WEKNORA_PORT_GRAPH_ENABLED"] == "false"
    assert env["WEKNORA_PORT_RERANK_TOP1_FLOORLESS"] == "true"
    assert env["WEKNORA_PORT_ROUTER_MODE"] == "DYNAMIC"
    assert "synthetic%40password%3Aonly" in env["WEKNORA_PORT_DATABASE_URL"]


@pytest.mark.parametrize("key,value", [("WEKNORA_PORT_DATABASE_SYSTEM_ID", "0"),
                                     ("WEKNORA_PORT_REDIS_RUN_ID", "0" * 40),
                                     ("WEKNORA_PORT_DATABASE_NAME", "old_database"),
                                     ("WEKNORA_PORT_GRAPH_ENABLED", "true")])
def test_observed_new_resources_and_no_nightly_experiment_override(tmp_path, key, value):
    env = base_env(tmp_path)
    env[key] = value
    with pytest.raises(RuntimeConfigurationError):
        runtime_environment(env)


def test_server_identity_cannot_bootstrap_or_borrow_daily_tenant(tmp_path):
    source = tmp_path / "principal.json"
    subject = "p8-real-synthetic-contract-only"
    row = {"tenant_id": 332, "subject_id": subject, "subject_sha256": hashlib.sha256(subject.encode()).hexdigest()}
    source.write_text(json.dumps(row))
    assert trusted_principal(source).tenant_id == 332
    source.write_text(json.dumps({**row, "tenant_id": 1}))
    with pytest.raises(RuntimeConfigurationError, match="TENANT332"):
        trusted_principal(source)
    source.write_text(json.dumps({**row, "subject_sha256": "f" * 64}))
    with pytest.raises(RuntimeConfigurationError, match="FINGERPRINT"):
        trusted_principal(source)


def test_private_token_file_not_vite_or_public_config(tmp_path):
    path = tmp_path / "api_token"
    path.write_text("contract-token-" + "a" * 32)
    assert api_token({"S8_API_TOKEN_FILE": str(path)}).startswith("contract-token")
    path.write_text("invalid token")
    with pytest.raises(RuntimeConfigurationError, match="TOKEN"):
        api_token({"S8_API_TOKEN_FILE": str(path)})


def test_guard_protects_all_routes_and_stream_recovery_without_tenant_headers():
    called = []
    async def application(scope, receive, send):
        called.append(scope["path"])
        await PrivateApiGuard.response(send, 200, {"ok": True})
    async def scenario():
        app = PrivateApiGuard(application, "a" * 40)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path in ("/healthz", "/readyz", "/api/v1/runtime", "/api/v1/knowledge-bases", "/api/v1/sessions/continue-stream/x"):
                assert (await client.get(path)).status_code == 401
                assert (await client.get(path, headers={"Authorization": "Bearer wrong", "X-Tenant-ID": "332"})).status_code == 401
                assert (await client.get(path, headers={"Authorization": "Bearer " + "a" * 40})).status_code == 200
            assert (await client.get("/readyz", headers=[("Authorization", "Bearer " + "a" * 40),
                                                       ("Authorization", "Bearer " + "a" * 40)])).status_code == 401
    asyncio.run(scenario())
    assert len(called) == 5


def test_compose_isolated_loopback_closed_and_same_worker_identity():
    compose = yaml.safe_load((ROOT / "deploy/s8/compose.yml").read_text(encoding="utf8"))
    assert compose["name"] == "rag-s8"
    services = compose["services"]
    assert set(services) == {"db", "redis", "api", "worker", "frontend"}
    for service in services.values():
        assert all(port.startswith("127.0.0.1:") for port in service.get("ports", []))
    api, worker = services["api"]["environment"], services["worker"]["environment"]
    for key in ("WEKNORA_PORT_REDIS_URL", "WEKNORA_PORT_REDIS_PREFIX", "WEKNORA_PORT_DATABASE_SYSTEM_ID", "S8_PRINCIPAL_FILE"):
        assert api[key] == worker[key]
    assert api["WEKNORA_PORT_CLOUD_ALLOWED"] == worker["WEKNORA_PORT_CLOUD_ALLOWED"] == "false"
    assert services["worker"]["profiles"] == ["worker"]
    assert "58bf87d2a6f1e72" in services["db"]["image"]
    assert services["api"]["secrets"][-1]["target"] == "principal.json"
    assert services["frontend"]["secrets"][-1]["target"] == "ui.htpasswd"


def test_api_dependency_subset_versions_match_existing_pyproject():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf8"))["project"]
    allowed = set(project["dependencies"] + project["optional-dependencies"]["weknora-port"])
    requirements = (ROOT / "deploy/s8/requirements.txt").read_text().splitlines()
    assert all(line in allowed for line in requirements if line and not line.startswith("#"))
    frontend = (ROOT / "deploy/s8/Dockerfile.frontend").read_text()
    assert "VITE_WEKNORA_API_BASE=/api/v1" in frontend
    assert "TOKEN" not in frontend

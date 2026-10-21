"""Offline safety tests for the local runtime launcher.

All process and HTTP interactions are mocked; these tests never contact services.
"""
from __future__ import annotations

import contextlib
import asyncio
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import local_runtime


def _runtime_config(**overrides):
    config = {
        "python": r"E:\RAG quention\.venv\Scripts\python.exe",
        "node": r"C:\Program Files\nodejs\node.exe",
        "state_dir": r"E:\WK\run\p81-retirement-20261018\launcher-tests\state",
        "api_port": 56431,
        "ui_port": 56432,
        "environment": {
            "WEKNORA_PORT_MINERU_ENABLED": "true",
            "WEKNORA_PORT_DATABASE_URL": "postgresql://127.0.0.1/test",
        },
        "containers": [
            {"service": "postgres", "id": "a" * 64},
            {"service": "redis", "id": "b" * 64},
        ],
    }
    config.update(overrides)
    return config


def _write_config(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_load_config_accepts_explicit_local_runtime_paths(tmp_path):
    config = local_runtime.load_config(_write_config(tmp_path, _runtime_config()))

    assert Path(config["python"]).is_absolute()
    assert Path(config["node"]).is_absolute()
    assert Path(config["state_dir"]).drive.upper() == "E:"
    assert {row["service"] for row in config["containers"]} == {"postgres", "redis"}


@pytest.mark.parametrize(
    ("override", "expected_error"),
    [
        ({"state_dir": r"C:\temp\runtime"}, "STATE_MUST_USE_D_OR_E"),
        ({"ui_port": 56431}, "DISTINCT_LOCAL_PORTS_REQUIRED"),
        (
            {
                "environment": {
                    "WEKNORA_PORT_DATABASE_URL": "postgresql://127.0.0.1/test"
                }
            },
            "MINERU_REQUIRED",
        ),
    ],
)
def test_load_config_rejects_invalid_local_runtime_fields(
    tmp_path, override, expected_error
):
    with pytest.raises(RuntimeError, match=expected_error):
        local_runtime.load_config(_write_config(tmp_path, _runtime_config(**override)))


@pytest.mark.parametrize(
    "body",
    [
        {"data": {"implementation": "legacy-backend"}},
        {"data": {"implementation": None}},
    ],
)
def test_api_identity_rejects_wrong_implementation(monkeypatch, body):
    payload = io.BytesIO(json.dumps(body).encode("utf-8"))
    monkeypatch.setattr(
        local_runtime.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: contextlib.nullcontext(payload),
    )

    with pytest.raises(RuntimeError, match="API_IMPLEMENTATION_MISMATCH"):
        local_runtime.api_identity(56431)


@pytest.mark.parametrize("body", [{}, {"data": {}}])
def test_api_identity_rejects_missing_identity(monkeypatch, body):
    payload = io.BytesIO(json.dumps(body).encode("utf-8"))
    monkeypatch.setattr(
        local_runtime.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: contextlib.nullcontext(payload),
    )

    with pytest.raises(RuntimeError, match="API_IMPLEMENTATION_MISMATCH"):
        local_runtime.api_identity(56431)


def test_reused_pid_with_changed_identity_is_never_stopped(monkeypatch):
    saved = {"pid": 3456, "exe": r"E:\app\.venv\Scripts\python.exe", "created": 10, "command_sha256": "old"}
    state = {"processes": {"api": saved}}
    calls = []
    monkeypatch.setattr(
        local_runtime,
        "process_identity",
        lambda _pid: {**saved, "created": 11, "command_sha256": "new"},
    )
    monkeypatch.setattr(local_runtime.subprocess, "run", lambda *a, **kw: calls.append((a, kw)))

    with pytest.raises(RuntimeError, match="PROCESS_IDENTITY_CHANGED_REFUSE_STOP_API"):
        local_runtime.stop_owned(state)

    assert calls == []


def test_matching_owned_process_is_stopped(monkeypatch):
    saved = {"pid": 3456, "exe": r"E:\app\.venv\Scripts\python.exe", "created": 10, "command_sha256": "same"}
    calls = []
    monkeypatch.setattr(local_runtime, "process_identity", lambda _pid: dict(saved))
    monkeypatch.setattr(
        local_runtime.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0),
    )

    stopped = local_runtime.stop_owned({"processes": {"api": saved}})

    assert stopped == ["api"]
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0][-1] == "Stop-Process -Id 3456 -ErrorAction Stop"
    assert kwargs["check"] is True


def test_environment_discards_inherited_runtime_overrides_and_sets_new_ui_api(monkeypatch):
    monkeypatch.setenv("VITE_WEKNORA_API_BASE", "http://127.0.0.1:8000")
    monkeypatch.setenv("VITE_OLD_FRONTEND_FLAG", "stale")
    monkeypatch.setenv("WEKNORA_PORT_DATABASE_URL", "postgresql://old/old")
    monkeypatch.setenv("WEKNORA_PORT_OLD_REDIS_URL", "redis://old/15")
    cfg = {
        "api_port": 56431,
        "ui_port": 56432,
        "environment": {
            "WEKNORA_PORT_DATABASE_URL": "postgresql://127.0.0.1/new",
            "WEKNORA_PORT_MINERU_ENABLED": "true",
        },
    }

    env = local_runtime.environment(cfg, redis_run_id="fresh-run-id")

    assert env["VITE_WEKNORA_API_BASE"] == "http://127.0.0.1:56431"
    assert env["WEKNORA_PORT_ORIGINS"] == "http://127.0.0.1:56432"
    assert env["WEKNORA_PORT_DATABASE_URL"] == "postgresql://127.0.0.1/new"
    assert env["WEKNORA_PORT_REDIS_RUN_ID"] == "fresh-run-id"
    assert "VITE_OLD_FRONTEND_FLAG" not in env
    assert "WEKNORA_PORT_OLD_REDIS_URL" not in env
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_collect_usage_is_read_only_scoped_and_keeps_malformed_usage_unreported(monkeypatch):
    statements = []
    rows = [
        {"provider": "local", "model": "qwen", "capability": "cheap", "outcome": "RESPONSE_RECEIVED",
         "usage": {"status": "REPORTED", "prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}},
        {"provider": "local", "model": "qwen", "capability": "cheap", "outcome": "UNKNOWN", "usage": None},
        {"provider": "local", "model": "qwen", "capability": "cheap", "outcome": "RESPONSE_RECEIVED",
         "usage": {"status": "REPORTED", "prompt_tokens": -1, "completion_tokens": 1, "total_tokens": 0}},
        {"provider": "local", "model": "qwen", "capability": "cheap", "outcome": "RESPONSE_RECEIVED",
         "usage": {"status": "REPORTED", "prompt_tokens": None, "completion_tokens": None, "total_tokens": 7}},
        {"provider": "other", "model": "unknown", "capability": "embedding", "outcome": "UNKNOWN", "usage": {}},
    ]

    class Cursor:
        def __init__(self, value):
            self.value = value

        async def fetchone(self):
            return self.value

        async def fetchall(self):
            return self.value

    class Connection:
        async def execute(self, sql, params=None):
            statements.append((sql, params))
            if sql.startswith("SELECT system_identifier"):
                return Cursor({"system_id": "12345", "database": "weknora_local"})
            if sql.startswith("SELECT tenant_id,subject_id"):
                return Cursor({"tenant_id": 42, "subject_id": "local-user"})
            if sql.startswith("SELECT provider"):
                return Cursor(rows)
            if sql.startswith("SELECT id,role,name,source,status,parameters"):
                return Cursor([{"id": "m1", "role": "cheap", "name": "Local Qwen",
                                "source": "local", "status": "active", "provider": "ollama"}])
            if sql.startswith("SELECT id,name,cloud_allowed"):
                return Cursor([{"id": "kb1", "name": "Notes", "cloud_allowed": False}])
            return Cursor(None)

        async def close(self):
            pass

    async def connect(*args, **kwargs):
        assert args == ("postgresql://127.0.0.1/test",)
        assert kwargs["autocommit"] is True
        return Connection()

    psycopg = SimpleNamespace(AsyncConnection=SimpleNamespace(connect=connect))
    rows_module = SimpleNamespace(dict_row=object())
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows_module)
    cfg = _runtime_config(environment={
        "WEKNORA_PORT_MINERU_ENABLED": "true",
        "WEKNORA_PORT_DATABASE_URL": "postgresql://127.0.0.1/test",
        "WEKNORA_PORT_DATABASE_SYSTEM_ID": "12345",
        "WEKNORA_PORT_DATABASE_NAME": "weknora_local",
    })

    report = asyncio.run(local_runtime.collect_usage(cfg))

    assert report["calls"] == 5
    assert report["reported"] == 2
    assert report["unreported"] == 3
    assert report["unknown_outcome"] == 2
    assert report["groups"][0]["prompt_tokens"] is None
    assert report["groups"][0]["completion_tokens"] is None
    assert report["groups"][0]["total_tokens"] == 21
    assert report["groups"][1]["prompt_tokens"] is None
    assert report["groups"][1]["completion_tokens"] is None
    assert report["groups"][1]["total_tokens"] is None
    assert report["models"] == [{"id": "m1", "role": "cheap", "name": "Local Qwen",
                                 "source": "local", "status": "active", "provider": "ollama"}]
    assert report["knowledge_bases"] == [{"id": "kb1", "name": "Notes", "cloud_allowed": False}]
    query, scope = next(item for item in statements if item[0].startswith("SELECT provider"))
    assert "WHERE tenant_id=%s AND subject_id=%s" in query
    assert scope == (42, "local-user")
    model_query, model_scope = next(item for item in statements if item[0].startswith("SELECT id,role,name,source"))
    assert "WHERE tenant_id=%s AND subject_id=%s AND status='active' AND deleted_at IS NULL" in model_query
    assert "parameters->>'provider' AS provider" in model_query
    assert "parameters FROM" not in model_query
    assert model_scope == (42, "local-user")
    kb_query, kb_scope = next(item for item in statements if item[0].startswith("SELECT id,name,cloud_allowed"))
    assert "WHERE tenant_id=%s AND deleted_at IS NULL" in kb_query
    assert kb_scope == (42,)
    assert statements[0][0] == "BEGIN TRANSACTION READ ONLY"
    assert statements[-1][0] == "ROLLBACK"
    assert all(not sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"))
               for sql, _ in statements)

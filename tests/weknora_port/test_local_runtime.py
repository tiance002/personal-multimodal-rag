"""Offline safety tests for the local runtime launcher.

All process and HTTP interactions are mocked; these tests never contact services.
"""
from __future__ import annotations

import contextlib
import io
import json
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

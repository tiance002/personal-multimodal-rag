from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "s8_6_5_langfuse_probe.py"
SPEC = importlib.util.spec_from_file_location("s8_6_5_langfuse_probe", SCRIPT)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def _env_file(path: Path) -> Path:
    path.write_text(
        "\n".join(
            (
                "LANGFUSE_PUBLIC_KEY=pk-never-print-public",
                "LANGFUSE_SECRET_KEY=sk-never-print-secret",
                "LANGFUSE_BASE_URL=https://us.cloud.langfuse.com",
                "RAG_LANGFUSE_CAPTURE_CONTENT=false",
                "RAG_LANGFUSE_ENABLED=false",
                "RAG_CLOUD_ENABLED=false",
            )
        ),
        encoding="utf-8",
    )
    return path


def test_requires_explicit_flag_before_reading_or_sending(tmp_path: Path, capsys) -> None:
    output = tmp_path / "result.json"
    env_file = _env_file(tmp_path / "local.env")

    assert probe.main(["--env-file", str(env_file), "--output", str(output)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "BLOCKED_EXPLICIT_FLAG_REQUIRED"
    assert not output.exists()


def test_existing_output_blocks_before_any_sdk_access(tmp_path: Path, capsys, monkeypatch) -> None:
    output = tmp_path / "result.json"
    output.write_text("keep", encoding="utf-8")
    env_file = _env_file(tmp_path / "local.env")

    class ForbiddenSDK:
        def __init__(self, **kwargs):
            raise AssertionError("SDK must not be constructed for an existing output")

    monkeypatch.setitem(sys.modules, "langfuse", types.SimpleNamespace(Langfuse=ForbiddenSDK))
    assert probe.main(["--allow-cloud-probe", "--env-file", str(env_file), "--output", str(output)]) == 2
    assert capsys.readouterr().out.find("BLOCKED_RESULT_EXISTS") >= 0
    assert output.read_text(encoding="utf-8") == "keep"


def test_auth_then_persist_planned_identity_before_metadata_emission(tmp_path: Path, capsys, monkeypatch) -> None:
    output = tmp_path / "result.json"
    env_file = _env_file(tmp_path / "local.env")
    events: list[str] = []

    class Observation:
        def __enter__(self):
            saved = json.loads(output.read_text(encoding="utf-8"))
            assert saved["status"] == "PLANNED_NOT_SENT"
            assert saved["send_state"] == "OBSERVATION_IN_PROGRESS_SEND_UNKNOWN"
            assert saved["trace_id"]
            assert saved["nonce"]
            events.append("observation_enter")

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeLangfuse:
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == "https://us.cloud.langfuse.com"
            assert kwargs["tracing_enabled"] is True
            assert kwargs["public_key"] == "pk-never-print-public"
            assert kwargs["secret_key"] == "sk-never-print-secret"
            events.append("constructed")
            self.trace_id = None

        def auth_check(self):
            events.append("auth_check")
            return True

        def start_as_current_observation(self, **kwargs):
            assert kwargs["as_type"] == "span"
            assert "input" not in kwargs and "output" not in kwargs
            assert kwargs["metadata"]["provider_calls"] == 0
            assert kwargs["metadata"]["llm_generations"] == 0
            self.trace_id = kwargs["trace_context"]["trace_id"]
            return Observation()

        def get_current_trace_id(self):
            return self.trace_id

        def flush(self):
            events.append("flush")

        def shutdown(self):
            events.append("shutdown")

    monkeypatch.setitem(sys.modules, "langfuse", types.SimpleNamespace(Langfuse=FakeLangfuse))
    assert probe.main(["--allow-cloud-probe", "--env-file", str(env_file), "--output", str(output)]) == 0
    stdout = capsys.readouterr().out
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert events == ["constructed", "auth_check", "observation_enter", "flush", "shutdown"]
    assert saved["status"] == "TRACE_FLUSHED_PENDING_READBACK"
    assert saved["auth_check"] is True
    assert saved["provider_calls"] == saved["llm_generations"] == 0
    assert "pk-never-print-public" not in stdout and "sk-never-print-secret" not in stdout
    assert "pk-never-print-public" not in output.read_text(encoding="utf-8")
    assert "sk-never-print-secret" not in output.read_text(encoding="utf-8")


def test_auth_failure_never_emits_observation(tmp_path: Path, capsys, monkeypatch) -> None:
    output = tmp_path / "result.json"
    env_file = _env_file(tmp_path / "local.env")

    class FakeLangfuse:
        def __init__(self, **kwargs):
            pass

        def auth_check(self):
            return False

        def shutdown(self):
            pass

    monkeypatch.setitem(sys.modules, "langfuse", types.SimpleNamespace(Langfuse=FakeLangfuse))
    assert probe.main(["--allow-cloud-probe", "--env-file", str(env_file), "--output", str(output)]) == 2
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "BLOCKED_AUTH_CHECK_FAILED"
    assert result["send_state"] == "NOT_STARTED"
    assert "trace_id" in result and result["trace_id"] is None
    assert "sk-never-print-secret" not in capsys.readouterr().out

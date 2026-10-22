"""Explicitly gated synthetic Langfuse trace probe; no RAG or LLM/provider imports."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ALLOWED_KEYS = {
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_AUTHORIZATION",
    "LANGFUSE_BASE_URL",
    "RAG_LANGFUSE_CAPTURE_CONTENT",
}
EXPECTED_HOST = "us.cloud.langfuse.com"
ENVIRONMENT = "s865-diagnostic"


def _load_credentials(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not match or match.group(1) not in ALLOWED_KEYS:
            continue
        raw = match.group(2)
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        values[match.group(1)] = raw
    return values


def _fingerprint(value: str) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else None


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, separators=(",", ":"), ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-cloud-probe", action="store_true", help="explicitly authorize one metadata-only Cloud trace")
    parser.add_argument("--env-file", type=Path, required=True, help="credential/config file; values are never printed")
    parser.add_argument("--output", type=Path, required=True, help="new result file; stores only sanitized receipt, never credentials")
    args = parser.parse_args(argv)
    result: dict[str, object] = {
        "schema": "s8-6-5-langfuse-probe/v1",
        "environment": ENVIRONMENT,
        "provider_calls": 0,
        "llm_generations": 0,
        "input_included": False,
        "output_included": False,
        "readback": "PENDING",
    }
    if not args.allow_cloud_probe:
        result.update(status="BLOCKED_EXPLICIT_FLAG_REQUIRED", trace_id=None)
        print(json.dumps(result, separators=(",", ":")))
        return 2
    if args.output.exists():
        result.update(status="BLOCKED_RESULT_EXISTS", trace_id=None)
        print(json.dumps(result, separators=(",", ":")))
        return 2
    if not args.env_file.is_file():
        result.update(status="BLOCKED_ENV_FILE_MISSING", trace_id=None)
        print(json.dumps(result, separators=(",", ":")))
        return 2

    try:
        values = _load_credentials(args.env_file)
        host = urlsplit(values.get("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")).hostname
        public_key = values.get("LANGFUSE_PUBLIC_KEY", "").strip()
        secret_key = values.get("LANGFUSE_SECRET_KEY", "").strip()
        result["endpoint_host"] = host
        result["capture_content_enabled"] = values.get("RAG_LANGFUSE_CAPTURE_CONTENT", "").lower() == "true"
        result["key_fingerprints"] = {
            "public": _fingerprint(public_key),
            "secret": _fingerprint(secret_key),
        }
        if host != EXPECTED_HOST:
            result.update(status="BLOCKED_UNEXPECTED_ENDPOINT_HOST", trace_id=None)
            print(json.dumps(result, separators=(",", ":")))
            return 2
        if result["capture_content_enabled"]:
            result.update(status="BLOCKED_CAPTURE_CONTENT_POLICY", trace_id=None)
            print(json.dumps(result, separators=(",", ":")))
            return 2
        if not public_key or not secret_key:
            result.update(status="BLOCKED_LANGFUSE_CREDENTIALS_MISSING", trace_id=None)
            print(json.dumps(result, separators=(",", ":")))
            return 2

        # Load the pinned SDK only after reading credentials; never print values.
        from langfuse import Langfuse

        result["sdk_version"] = __import__("importlib.metadata", fromlist=["version"]).version("langfuse")
        nonce = uuid.uuid4().hex
        trace_id = uuid.uuid4().hex
        result.update(
            status="PLANNED_NOT_SENT",
            trace_id=trace_id,
            nonce=nonce,
            send_state="NOT_STARTED",
        )
        _atomic_json(args.output, result)
        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=values.get("LANGFUSE_BASE_URL", f"https://{EXPECTED_HOST}"),
            environment=ENVIRONMENT,
            release="s8-6-5-zero-llm-probe",
            tracing_enabled=True,
        )
        try:
            try:
                authenticated = bool(client.auth_check())
            except Exception as exc:
                result.update(status="BLOCKED_AUTH_CHECK_FAILED", auth_check=False, error_class=type(exc).__name__, trace_id=None)
                result["send_state"] = "NOT_STARTED"
                _atomic_json(args.output, result)
                print(json.dumps(result, separators=(",", ":")))
                return 2
            result["auth_check"] = authenticated
            if not authenticated:
                result.update(status="BLOCKED_AUTH_CHECK_FAILED", trace_id=None)
                result["send_state"] = "NOT_STARTED"
                _atomic_json(args.output, result)
                print(json.dumps(result, separators=(",", ":")))
                return 2
            _atomic_json(args.output, result)
            result["send_state"] = "OBSERVATION_IN_PROGRESS_SEND_UNKNOWN"
            _atomic_json(args.output, result)
            with client.start_as_current_observation(
                trace_context={"trace_id": trace_id},
                name="s8-6-5-zero-llm-connectivity-probe",
                as_type="span",
                metadata={
                    "environment": ENVIRONMENT,
                    "purpose": "synthetic-connectivity-check",
                    "nonce": nonce,
                    "provider_calls": 0,
                    "llm_generations": 0,
                },
            ):
                current_trace_id = client.get_current_trace_id()
                if current_trace_id != trace_id:
                    raise RuntimeError("TRACE_ID_MISMATCH")
            result["send_state"] = "OBSERVATION_QUEUED_READBACK_REQUIRED"
            _atomic_json(args.output, result)
            client.flush()
        finally:
            client.shutdown()
        result.update(
            status="TRACE_FLUSHED_PENDING_READBACK",
            send_state="FLUSH_RETURNED_READBACK_REQUIRED",
        )
        _atomic_json(args.output, result)
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except Exception as exc:  # output class only; SDK exceptions may contain sensitive request details
        result.update(status="PROBE_FAILED", error_class=type(exc).__name__)
        if result.get("send_state") in ("NOT_STARTED", None):
            result["send_state"] = "NOT_STARTED"
        else:
            result["send_state"] = "UNKNOWN_DO_NOT_RETRY_WITHOUT_READBACK"
        if args.output.exists():
            _atomic_json(args.output, result)
        print(json.dumps(result, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Short-lived, bearer-protected normal FastAPI entry for frozen M4 tenant 332.

The launcher pins the principal from the P25 state file on the server side,
uses the current verified local database/key configuration, binds only to
127.0.0.1, and never persists its short-lived bearer token.  The HTTP client
below intentionally exposes only ordinary API operations: it has no tenant,
subject, model-override, or Gold payload fields.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_CONFIG = Path(r"E:\WK\run\p81-retirement-20261018\local.json")
DEFAULT_P25_STATE = Path(r"E:\WK\run\p25-20261017\runtime\state.json")
DEFAULT_PLAN = ROOT / "docs/milestones/RAG_M4_0_BASELINE.plan.json"
DEFAULT_OUTPUT = Path(r"E:\WK\run\rag-m40-tenant332-api")
EXPECTED_SYSTEM_ID = "7696088403504697382"
EXPECTED_DATABASE = "weknora_python_port_20261013"
EXPECTED_REDIS_RUN_ID = "dcff9d65a25725574ba0cfbc743d409fac211ef0"
TARGET_KBS = frozenset({
    "f4b6ddb2-c05c-4ece-ba73-c1dad6eea8d7",
    "214b8b2f-84f9-4d75-ab79-eccbdcd50ec2",
    "76770fba-17c3-4704-8663-10aa669476ce",
    "96a9eebd-ffb2-4bc6-9bea-3ed015845479",
    "7f1d720d-9561-446f-a8cc-6a562c339d23",
    "b66d8ae4-8cfe-4c9b-86d8-c28cb66ca3ef",
})
TOKEN_TTL_SECONDS = 45 * 60


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError("IDENTITY_STATE_INVALID")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _subject_fingerprint(subject: str) -> str:
    import hashlib
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()[:16]


def _case_bindings(plan: Mapping[str, Any], principal_tenant: int,
                   subject: str) -> dict[str, dict[str, dict[str, str]]]:
    cases = plan.get("cases")
    if not isinstance(cases, list) or not cases:
        raise RuntimeError("M4_FROZEN_CASES_MISSING")
    result: dict[str, dict[str, dict[str, str]]] = {}
    seen_kbs: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise RuntimeError("M4_FROZEN_CASE_INVALID")
        if case.get("tenant_id") != principal_tenant or case.get("subject_id") != subject:
            raise RuntimeError("M4_FROZEN_PRINCIPAL_MISMATCH")
        kb_id = case.get("knowledge_base_id")
        if not isinstance(kb_id, str) or not kb_id:
            raise RuntimeError("M4_FROZEN_KB_BINDING_INVALID")
        seen_kbs.add(kb_id)
        docs = case.get("expected_documents") or []
        bucket = result.setdefault(kb_id, {})
        for doc in docs:
            if isinstance(doc, dict) and isinstance(doc.get("id"), str):
                binding = {"source_version": doc.get("source_version", ""),
                           "file_sha256": doc.get("file_sha256", "")}
                prior = bucket.get(doc["id"])
                if prior is not None and prior != binding:
                    raise RuntimeError("M4_FROZEN_DOCUMENT_BINDING_CONFLICT")
                if not all(binding.values()):
                    raise RuntimeError("M4_FROZEN_DOCUMENT_VERSION_HASH_REQUIRED")
                bucket[doc["id"]] = binding
    if seen_kbs != TARGET_KBS:
        raise RuntimeError("M4_FROZEN_KB_SET_MISMATCH")
    return result


def _read_only_preflight(config_path: Path, p25_state_path: Path,
                         plan_path: Path) -> dict[str, Any]:
    import hashlib
    import psycopg
    from psycopg.rows import dict_row
    from redis import Redis
    from scripts.local_runtime import environment, load_config
    from backend.weknora_port.development import DevelopmentSettings

    state = _read_json(p25_state_path)
    tenant = state.get("tenant")
    subject = state.get("subject")
    if tenant != 332 or not isinstance(subject, str) or not subject.startswith("p8-real-"):
        raise RuntimeError("P25_TRUSTED_IDENTITY_MISSING")
    plan = _read_json(plan_path)
    case_docs = _case_bindings(plan, tenant, subject)

    cfg = load_config(str(config_path))
    base_env = environment(cfg)
    redis = Redis.from_url(base_env["WEKNORA_PORT_REDIS_URL"], decode_responses=True)
    try:
        redis_info = redis.info("server")
        redis_run_id = redis_info.get("run_id", "")
    finally:
        redis.close()
    if redis_run_id != EXPECTED_REDIS_RUN_ID or redis_run_id != state.get("redis_run_id"):
        raise RuntimeError("REDIS_PHYSICAL_IDENTITY_MISMATCH")
    settings = DevelopmentSettings.from_environment(environment(cfg, redis_run_id))
    if settings.database != EXPECTED_DATABASE or settings.system_id != EXPECTED_SYSTEM_ID:
        raise RuntimeError("POSTGRES_CONFIG_IDENTITY_MISMATCH")

    p25_key_path = Path(state["secret_key_file"]).resolve()
    current_key_path = settings.secret_key_file.resolve()
    p25_key = p25_key_path.read_bytes()
    current_key = current_key_path.read_bytes()
    same_key = len(p25_key) == 32 and len(current_key) == 32 and hmac.compare_digest(p25_key, current_key)
    if not same_key:
        raise RuntimeError("CURRENT_MODEL_SECRET_KEY_MISMATCH")

    marker = Path(state["storage_root"]).resolve() / ".weknora-python-storage.json"
    if not marker.is_file():
        raise RuntimeError("P25_SOURCE_STORAGE_MARKER_MISSING")
    marker_value = _read_json(marker)
    if marker_value.get("database") != EXPECTED_DATABASE or marker_value.get("system_id") != EXPECTED_SYSTEM_ID:
        raise RuntimeError("P25_SOURCE_STORAGE_IDENTITY_MISMATCH")

    tenant1 = None
    with psycopg.connect(settings.database_url, autocommit=True, row_factory=dict_row,
                         connect_timeout=5) as conn:
        conn.execute("BEGIN TRANSACTION READ ONLY")
        identity = conn.execute(
            "SELECT system_identifier::text AS system_id,current_database() AS database "
            "FROM pg_control_system()"
        ).fetchone()
        if identity["system_id"] != EXPECTED_SYSTEM_ID or identity["database"] != EXPECTED_DATABASE:
            raise RuntimeError("POSTGRES_PHYSICAL_IDENTITY_MISMATCH")
        tenant_row = conn.execute("SELECT deleted_at FROM tenants WHERE id=%s", (tenant,)).fetchone()
        if not tenant_row or tenant_row["deleted_at"] is not None:
            raise RuntimeError("TENANT_332_UNAVAILABLE")
        config = conn.execute(
            "SELECT roles FROM port_model_configuration WHERE tenant_id=%s AND subject_id=%s",
            (tenant, subject),
        ).fetchone()
        if not config:
            raise RuntimeError("P25_SUBJECT_MODEL_CONFIGURATION_MISSING")
        local_principal = conn.execute(
            "SELECT tenant_id,subject_id FROM port_local_principal WHERE slot='local'"
        ).fetchone()
        if not local_principal or local_principal["tenant_id"] != 1:
            raise RuntimeError("DAILY_LOCAL_PRINCIPAL_CHANGED")
        target_rows = conn.execute(
            "SELECT id::text AS id,name,tenant_id,cloud_allowed,deleted_at IS NULL AS active "
            "FROM knowledge_bases WHERE id::text=ANY(%s::text[]) ORDER BY id",
            (sorted(TARGET_KBS),),
        ).fetchall()
        if {row["id"] for row in target_rows} != TARGET_KBS or any(
                row["tenant_id"] != tenant or not row["active"] for row in target_rows):
            raise RuntimeError("FROZEN_KB_OWNERSHIP_MISMATCH")
        registry_rows = conn.execute(
            "SELECT id,role,name,source,status,deleted_at IS NULL AS active "
            "FROM models WHERE tenant_id=%s AND subject_id=%s ORDER BY role,id",
            (tenant, subject),
        ).fetchall()
        provider_calls = conn.execute(
            "SELECT count(*) AS calls,count(*) FILTER (WHERE outcome='UNKNOWN') AS unknown "
            "FROM port_provider_calls WHERE tenant_id=%s AND subject_id=%s",
            (tenant, subject),
        ).fetchone()
        session_count = conn.execute(
            "SELECT count(*) AS n FROM sessions WHERE tenant_id=%s AND subject_id=%s AND deleted_at IS NULL",
            (tenant, subject),
        ).fetchone()["n"]
        memory_count = conn.execute(
            "SELECT (SELECT count(*) FROM memory_items WHERE tenant_id=%s AND subject_id=%s) + "
            "(SELECT count(*) FROM memory_subjects WHERE tenant_id=%s AND subject_id=%s) AS n",
            (tenant, subject, tenant, subject),
        ).fetchone()["n"]
        tenant1 = conn.execute(
            "SELECT id::text AS id FROM knowledge_bases WHERE tenant_id=1 AND deleted_at IS NULL ORDER BY id LIMIT 1"
        ).fetchone()
        expected_doc_ids = sorted({doc_id for bindings in case_docs.values() for doc_id in bindings})
        actual_docs = conn.execute(
            "SELECT id,tenant_id,knowledge_base_id,file_hash,metadata->>'source_version' AS source_version,"
            "parse_status,enable_status FROM knowledges WHERE id::text=ANY(%s::text[]) AND deleted_at IS NULL",
            (expected_doc_ids,),
        ).fetchall()
        actual_doc_map = {row["id"]: row for row in actual_docs}
        expected_doc_count = sum(len(bindings) for bindings in case_docs.values())
        if len(actual_doc_map) != expected_doc_count:
            raise RuntimeError("M4_FROZEN_DOCUMENTS_UNAVAILABLE")
        for kb_id, docs in case_docs.items():
            for doc_id, binding in docs.items():
                row = actual_doc_map[doc_id]
                if (row["tenant_id"] != tenant or row["knowledge_base_id"] != kb_id or
                        row["source_version"] != binding["source_version"] or
                        row["file_hash"] != binding["file_sha256"] or
                        row["parse_status"] != "completed" or row["enable_status"] != "enabled"):
                    raise RuntimeError("M4_FROZEN_DOCUMENT_VERSION_MISMATCH")
        conn.execute("ROLLBACK")

    # The M4 selection's document/version/hash bindings are already frozen by
    # rag_m40_preflight; here we retain the expected document IDs per API KB.
    return {
        "tenant_id": tenant,
        "subject_fingerprint": _subject_fingerprint(subject),
        "principal_source": str(p25_state_path),
        "p25_source_sha": state.get("source_sha"),
        "postgres_system_id": identity["system_id"],
        "database": identity["database"],
        "redis_run_id": redis_run_id,
        "redis_db": settings.redis_url.rsplit("/", 1)[-1],
        "p25_redis_prefix_matches_daily": state.get("redis_prefix") == settings.redis_prefix,
        "daily_principal_tenant_id": local_principal["tenant_id"],
        "daily_principal_subject_fingerprint": _subject_fingerprint(local_principal["subject_id"]),
        "daily_cloud_allowed": settings.cloud_allowed,
        "storage_marker_matches": True,
        "current_secret_key_matches_p25": True,
        "target_kbs": [{"id": row["id"], "name": row["name"],
                        "tenant_id": row["tenant_id"], "cloud_allowed": row["cloud_allowed"]}
                       for row in target_rows],
        "target_documents": case_docs,
        "registry_rows_before": [{k: row[k] for k in ("id", "role", "name", "source", "status", "active")}
                                  for row in registry_rows],
        "provider_calls_before": provider_calls["calls"],
        "unknown_calls_before": provider_calls["unknown"],
        "existing_subject_sessions": session_count,
        "existing_subject_memory_items_and_settings": memory_count,
        "tenant1_probe_kb_id": tenant1["id"] if tenant1 else None,
        "expected_provider_calls_this_task": 0,
    }


class BearerTokenGuard:
    """ASGI guard that authenticates every HTTP path, including SSE replay."""

    def __init__(self, app: Any, token: str, expires_at: float):
        if not token or len(token) < 32:
            raise ValueError("EVAL_API_TOKEN_INVALID")
        self.app = app
        self._token = token.encode("ascii")
        self._expires_at = expires_at

    def valid(self, headers: list[tuple[bytes, bytes]], now: float | None = None) -> bool:
        auth_values = [value.strip() for key, value in headers if key.lower() == b"authorization"]
        supplied = auth_values[0][7:] if len(auth_values) == 1 and auth_values[0].startswith(b"Bearer ") else b""
        return (len(auth_values) == 1 and (time.time() if now is None else now) < self._expires_at and
                hmac.compare_digest(supplied, self._token))

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not self.valid(scope.get("headers", [])):
            body = b'{"error":{"code":"EVAL_API_TOKEN_REQUIRED"}}'
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode("ascii")),
                                    (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": body})
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        await self.app(scope, receive, send)


@dataclass(frozen=True)
class SseEvent:
    event: str
    data: str


class M4Tenant332ApiClient:
    """Normal HTTP API client; identity is server-pinned, never client-supplied."""

    def __init__(self, base_url: str, token: str):
        from urllib.parse import urlsplit
        import httpx
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port or
                parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("M4_API_MUST_USE_LOOPBACK_HTTP")
        self._httpx = httpx
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.Client(timeout=30, follow_redirects=False)

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, path: str, *, authenticated: bool = True,
                headers: Mapping[str, str] | None = None, **kwargs):
        values = {"Origin": self.base_url}
        if authenticated:
            values["Authorization"] = "Bearer " + self._token
        if headers:
            values.update(headers)
        return self._client.request(method, self.base_url + path, headers=values, **kwargs)

    def get_json(self, path: str, **kwargs) -> dict[str, Any]:
        response = self.request("GET", path, **kwargs)
        response.raise_for_status()
        value = response.json()
        return value.get("data", value)

    def create_session(self, *, knowledge_base_scope: list[str], document_scope: list[str] | None = None,
                       title: str = "M4 evaluation") -> dict[str, Any]:
        return self._post_json("/api/v1/sessions", {
            "title": title, "knowledge_base_scope": knowledge_base_scope,
            "document_scope": document_scope or []})

    def _post_json(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        response = self.request("POST", path, json=dict(body))
        response.raise_for_status()
        value = response.json()
        return value.get("data", value)

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        value = self.get_json(f"/api/v1/sessions/{session_id}/messages")
        return value if isinstance(value, list) else value.get("items", [])

    def ask(self, session_id: str, *, question: str, request_id: str,
            knowledge_base_scope: list[str], document_scope: list[str] | None = None) -> list[SseEvent]:
        body = {"content": question, "request_id": request_id, "mode": "quick",
                "knowledge_base_scope": knowledge_base_scope, "document_scope": document_scope or []}
        events: list[SseEvent] = []
        with self._client.stream("POST", self.base_url + f"/api/v1/sessions/{session_id}/messages",
                                 headers={"Authorization": "Bearer " + self._token,
                                          "Origin": self.base_url, "Accept": "text/event-stream"},
                                 json=body, timeout=None) as response:
            response.raise_for_status()
            event_name = "message"
            data: list[str] = []
            for line in response.iter_lines():
                if not line:
                    if data:
                        events.append(SseEvent(event_name, "\n".join(data)))
                    event_name, data = "message", []
                elif line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
            if data:
                events.append(SseEvent(event_name, "\n".join(data)))
        return events

    def resume(self, session_id: str, assistant_message_id: str) -> list[SseEvent]:
        path = f"/api/v1/sessions/continue-stream/{session_id}"
        response_events: list[SseEvent] = []
        with self._client.stream("GET", self.base_url + path,
                                 params={"message_id": assistant_message_id},
                                 headers={"Authorization": "Bearer " + self._token,
                                          "Origin": self.base_url, "Accept": "text/event-stream"},
                                 timeout=None) as response:
            response.raise_for_status()
            name, data = "message", []
            for line in response.iter_lines():
                if not line:
                    if data:
                        response_events.append(SseEvent(name, "\n".join(data)))
                    name, data = "message", []
                elif line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
            if data:
                response_events.append(SseEvent(name, "\n".join(data)))
        return response_events


class M4EvaluationController:
    """Drive one frozen M4 case through the normal, server-pinned API.

    Gold and scoring fields are deliberately never placed in an HTTP request.
    Scoring remains the caller's post-response responsibility.
    """

    def __init__(self, client: M4Tenant332ApiClient):
        self.client = client

    def run_case(self, case: Mapping[str, Any]) -> dict[str, Any]:
        if (case.get("tenant_id") != 332 or
                case.get("knowledge_base_id") not in TARGET_KBS or
                not isinstance(case.get("question"), str) or not case["question"].strip()):
            raise ValueError("M4_CASE_SCOPE_INVALID")
        document_scope = case.get("document_scope", [])
        if not isinstance(document_scope, list) or any(not isinstance(x, str) or not x for x in document_scope):
            raise ValueError("M4_CASE_DOCUMENT_SCOPE_INVALID")
        request_id = str(uuid.uuid4())
        session = self.client.create_session(
            knowledge_base_scope=[case["knowledge_base_id"]],
            document_scope=document_scope,
            title="M4 evaluation " + str(case.get("case_id", "case")),
        )
        session_id = str(session.get("id", ""))
        if not session_id:
            raise RuntimeError("M4_API_SESSION_ID_MISSING")
        events = self.client.ask(
            session_id,
            question=case["question"],
            request_id=request_id,
            knowledge_base_scope=[case["knowledge_base_id"]],
            document_scope=document_scope,
        )
        history = self.client.messages(session_id)
        return {"case_id": case.get("case_id"), "session_id": session_id,
                "request_id": request_id, "events": events, "history": history}


@dataclass
class Tenant332ApiSession:
    client: M4Tenant332ApiClient = field(repr=False)
    base_url: str
    port: int
    redis_prefix: str
    process_id: int
    run_dir: Path
    identity_preflight: Mapping[str, Any] = field(repr=False)


@contextmanager
def tenant332_api(*, config_path: Path = DEFAULT_CONFIG,
                  p25_state_path: Path = DEFAULT_P25_STATE,
                  plan_path: Path = DEFAULT_PLAN,
                  output_root: Path = DEFAULT_OUTPUT,
                  execution_file: Path | None = None) -> Iterator[Tenant332ApiSession]:
    """Start an isolated API process and yield a normal HTTP client.

    No execution file means cloud sending stays globally disabled. A future
    authorized M4 batch may supply the existing M3.6 execution file explicitly;
    the launcher itself never creates or widens that file.
    """
    import httpx
    preflight = _read_only_preflight(config_path, p25_state_path, plan_path)
    run_dir = output_root / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True, exist_ok=False)
    config_data = _read_json(config_path)
    state = _read_json(p25_state_path)
    redis_prefix = "weknora-python-m4-tenant332:" + uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + TOKEN_TTL_SECONDS
    port = _available_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    summary_path = run_dir / "server-startup-summary.json"
    log_path = run_dir / "server.log"
    status_path = run_dir / "process-state.json"
    python = Path(config_data["python"])
    cmd = [str(python), "-B", str(ROOT / "scripts/m4_tenant332_api.py"), "_serve",
           "--config", str(config_path), "--p25-state", str(p25_state_path),
           "--redis-prefix", redis_prefix, "--port", str(port),
           "--expires-at", str(expires_at), "--startup-summary", str(summary_path)]
    env = os.environ.copy()
    from scripts.local_runtime import environment, load_config
    cfg = load_config(str(config_path))
    # Use the already-verified Redis instance identity from preflight. The child
    # verifies it again in assemble() before serving requests.
    env.update(environment(cfg, preflight["redis_run_id"]))
    env["WEKNORA_PORT_STORAGE_ROOT"] = str(Path(state["storage_root"]).resolve())
    env["WEKNORA_PORT_SECRET_KEY_FILE"] = str(Path(state["secret_key_file"]).resolve())
    env["WEKNORA_PORT_REDIS_PREFIX"] = redis_prefix
    env["WEKNORA_PORT_CLOUD_ALLOWED"] = "false"
    env["WEKNORA_PORT_ORIGINS"] = ""
    env.pop("WEKNORA_PORT_EXECUTION_FILE", None)
    if execution_file is not None:
        if not execution_file.is_file():
            raise RuntimeError("M3_6_EXECUTION_FILE_REQUIRED")
        env["WEKNORA_PORT_CLOUD_ALLOWED"] = "true"
        env["WEKNORA_PORT_EXECUTION_FILE"] = str(execution_file.resolve())
    env["M4_TENANT332_BEARER_TOKEN"] = token
    with log_path.open("ab", buffering=0) as log_file:
        process = subprocess.Popen(cmd, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log_file, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _write_json(status_path, {"status": "starting", "pid": process.pid,
                                  "started_at": _utc_now(), "port": port,
                                  "bind_host": "127.0.0.1", "redis_prefix": redis_prefix,
                                  "subject_fingerprint": preflight["subject_fingerprint"],
                                  "token_persisted": False, "worker_started": False})
        client = M4Tenant332ApiClient(base_url, token)
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("M4_TENANT332_API_PROCESS_EXITED_BEFORE_READY")
                try:
                    response = client.request("GET", "/readyz")
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.25)
            else:
                raise RuntimeError("M4_TENANT332_API_READY_TIMEOUT")
            _write_json(status_path, {"status": "running", "pid": process.pid,
                                      "started_at": _utc_now(), "port": port,
                                      "bind_host": "127.0.0.1", "redis_prefix": redis_prefix,
                                      "subject_fingerprint": preflight["subject_fingerprint"],
                                      "token_persisted": False, "worker_started": False})
            yield Tenant332ApiSession(client, base_url, port, redis_prefix, process.pid, run_dir, preflight)
        finally:
            client.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            _write_json(status_path, {"status": "stopped", "pid": process.pid,
                                      "started_at": _utc_now(), "stopped_at": _utc_now(),
                                      "exit_code": process.poll(), "port": port,
                                      "bind_host": "127.0.0.1", "redis_prefix": redis_prefix,
                                      "subject_fingerprint": preflight["subject_fingerprint"],
                                      "token_persisted": False, "worker_started": False})


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _verify_runtime(runtime, principal) -> dict[str, Any]:
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore
    required_roles = {"embedding", "rerank", "cheap", "expensive"}
    public = await runtime.read_model_configuration(principal)
    public_roles = {row["role"]: row for row in public.get("roles", [])}
    if not required_roles <= set(public_roles):
        raise RuntimeError("TENANT332_MODEL_ROLES_INCOMPLETE")
    for name in required_roles:
        row = public_roles[name]
        if not row.get("provider") or not row.get("model") or not row.get("key_configured"):
            raise RuntimeError("TENANT332_MODEL_ROLE_INCOMPLETE_" + name.upper())
        if row.get("credential_status") != "CONFIGURED":
            raise RuntimeError("TENANT332_MODEL_SECRET_UNAVAILABLE_" + name.upper())

    async with runtime.leases.lease() as guard:
        role_store = PostgresModelRoleStore(guard, principal, runtime.catalogue,
                                            cipher=runtime.cipher)
        _, roles, errors = await role_store.read_available()
    errors_by_role = dict(errors)
    if errors_by_role:
        raise RuntimeError("TENANT332_MODEL_SECRET_UNAVAILABLE")
    registry_ids = {}
    for role in roles:
        if role.role not in required_roles:
            continue
        model_id = runtime.models._identity(role)[0]
        # _load is read-only and validates tenant/subject, immutable model
        # identity, active status, and the registry's encrypted parameters.
        await runtime.models._load(model_id)
        registry_ids[role.role] = model_id
    if set(registry_ids) != required_roles:
        raise RuntimeError("TENANT332_MODEL_REGISTRY_ID_MISSING")
    return {
        "model_configuration_revision": str(public.get("revision", "")),
        "roles": {name: {"provider": public_roles[name]["provider"],
                         "model": public_roles[name]["model"],
                         "credential_status": public_roles[name]["credential_status"],
                         "key_configured": bool(public_roles[name]["key_configured"])}
                  for name in sorted(required_roles)},
        "registry_model_ids": registry_ids,
        "registry_load_validation": "PASS",
        "provider_requests": 0,
    }


def _serve(args) -> None:
    import uvicorn
    from backend.weknora_port.development import DevelopmentSettings, assemble
    from backend.weknora_port.entrypoint import create_app
    from backend.weknora_port.storage.history import LocalPrincipal

    token = os.environ.get("M4_TENANT332_BEARER_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("M4_TENANT332_API_TOKEN_MISSING")
    state = _read_json(Path(args.p25_state))
    if state.get("tenant") != 332 or not isinstance(state.get("subject"), str):
        raise RuntimeError("P25_TRUSTED_IDENTITY_MISSING")
    principal = LocalPrincipal(332, state["subject"])
    settings = DevelopmentSettings.from_environment()
    if settings.cloud_allowed and not settings.execution_file:
        raise RuntimeError("UNBOUNDED_CLOUD_API_DENIED")

    summary_path = Path(args.startup_summary)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def runtime_factory(active_settings):
        async with assemble(active_settings, principal=principal) as runtime:
            summary = await _verify_runtime(runtime, principal)
            _write_json(summary_path, {
                "status": "runtime_ready", "tenant_id": 332,
                "subject_fingerprint": _subject_fingerprint(principal.subject_id),
                "postgres_system_id": active_settings.system_id,
                "database": active_settings.database,
                "redis_run_id": active_settings.redis_run_id,
                "redis_prefix": active_settings.redis_prefix,
                "cloud_allowed": active_settings.cloud_allowed,
                "router_mode": active_settings.router_mode,
                "background_workers_started": False,
                **summary,
            })
            yield runtime

    app = create_app(settings=settings, runtime_factory=runtime_factory)
    guarded = BearerTokenGuard(app, token, float(args.expires_at))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", int(args.port)))
    sock.listen(128)
    sock.setblocking(False)
    config = uvicorn.Config(guarded, host="127.0.0.1", port=int(args.port),
                            log_level="warning", access_log=False, lifespan="on")
    server = uvicorn.Server(config)
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(server.serve(sockets=[sock]))
    finally:
        sock.close()


def _verify(args) -> dict[str, Any]:
    import psycopg
    from psycopg.rows import dict_row

    # The full source/version binding was already checked by the frozen M4
    # preflight. The API test checks active doc IDs through the normal routes.
    with tenant332_api(config_path=Path(args.config), p25_state_path=Path(args.p25_state),
                       plan_path=Path(args.plan),
                       output_root=Path(args.output)) as api_session:
        preflight = dict(api_session.identity_preflight)
        client = api_session.client
        no_token = client.request("GET", "/api/v1/runtime", authenticated=False)
        bad_token = client.request("GET", "/api/v1/runtime", headers={"Authorization": "Bearer invalid"})
        sse_no_token = client.request("GET", "/api/v1/sessions/continue-stream/not-a-session",
                                      authenticated=False, params={"message_id": "not-a-message"})
        if (no_token.status_code, bad_token.status_code, sse_no_token.status_code) != (401, 401, 401):
            raise RuntimeError("EVAL_API_BEARER_REJECTION_FAILED")

        runtime_identity = client.get_json("/api/v1/runtime")
        ready = client.get_json("/readyz")
        # The public runtime endpoint intentionally exposes only API identity
        # and version. Physical PG/Redis identity is verified from the child
        # runtime's non-secret startup summary written after trusted assembly.
        runtime_summary_path = api_session.run_dir / "server-startup-summary.json"
        runtime_summary = _read_json(runtime_summary_path)
        if (runtime_identity.get("implementation") != "weknora-python-port" or
                runtime_identity.get("api_version") != "v1" or
                ready.get("implementation") != "weknora-python-port" or
                ready.get("cloud_allowed") is not False or
                runtime_summary.get("status") != "runtime_ready" or
                runtime_summary.get("tenant_id") != 332 or
                runtime_summary.get("subject_fingerprint") != preflight["subject_fingerprint"] or
                runtime_summary.get("postgres_system_id") != EXPECTED_SYSTEM_ID or
                runtime_summary.get("database") != EXPECTED_DATABASE or
                runtime_summary.get("redis_run_id") != preflight["redis_run_id"] or
                runtime_summary.get("redis_prefix") != api_session.redis_prefix or
                runtime_summary.get("background_workers_started") is not False or
                runtime_summary.get("provider_requests") != 0):
            raise RuntimeError("EVAL_API_RUNTIME_IDENTITY_FAILED")

        # Client-supplied identity headers/query must have no effect.
        kbs_resp = client.request("GET", "/api/v1/knowledge-bases",
                                  headers={"X-Tenant-ID": "1", "X-Subject-ID": "untrusted"},
                                  params={"tenant_id": "1", "subject_id": "untrusted"})
        kbs_resp.raise_for_status()
        kbs_payload = kbs_resp.json().get("data", [])
        visible = {str(row["id"]): row for row in kbs_payload}
        if not TARGET_KBS <= set(visible):
            raise RuntimeError("TENANT332_KB_LIST_INCOMPLETE")
        if preflight["tenant1_probe_kb_id"] and preflight["tenant1_probe_kb_id"] in visible:
            raise RuntimeError("TENANT1_KB_LEAKED_TO_TENANT332")

        target_docs = preflight["target_documents"]
        api_documents = {}
        plan = _read_json(Path(args.plan))
        plan_docs = {}
        for case in plan["cases"]:
            kb = case["knowledge_base_id"]
            for doc in case.get("expected_documents", []):
                plan_docs.setdefault(kb, {})[doc["id"]] = doc
        for kb_id in sorted(TARGET_KBS):
            resp = client.request("GET", f"/api/v1/knowledge-bases/{kb_id}/documents")
            resp.raise_for_status()
            rows = resp.json().get("data", [])
            by_id = {str(row["id"]): row for row in rows}
            if not set(target_docs.get(kb_id, {})) <= set(by_id):
                raise RuntimeError("TENANT332_API_DOCUMENTS_INCOMPLETE")
            api_documents[kb_id] = {doc_id: {"status": by_id[doc_id].get("status"),
                                              "filename": by_id[doc_id].get("filename"),
                                              "source_version_verified_read_only": binding["source_version"]}
                                   for doc_id, binding in sorted(target_docs.get(kb_id, {}).items())}

        # Model settings pass through the real public configuration endpoint;
        # it decrypt-checks every saved key but never emits the key itself.
        model_resp = client.request("GET", "/api/v1/settings/model-configuration")
        model_resp.raise_for_status()
        model_public = model_resp.json().get("data", {})
        public_roles = {row["role"]: row for row in model_public.get("roles", [])}
        for role in ("cheap", "expensive", "embedding", "rerank"):
            if public_roles.get(role, {}).get("credential_status") != "CONFIGURED":
                raise RuntimeError("TENANT332_PUBLIC_MODEL_CONFIG_NOT_READY_" + role.upper())
        if any("api_key" in row or "secret" in json.dumps(row).lower() for row in public_roles.values()):
            raise RuntimeError("MODEL_SECRET_LEAKED_BY_PUBLIC_API")

        tenant1_kb = preflight["tenant1_probe_kb_id"]
        first_kb = sorted(TARGET_KBS)[0]
        body_identity = client.request("POST", "/api/v1/sessions", json={
            "title": "M4 denied body identity override",
            "knowledge_base_scope": [first_kb], "document_scope": [],
            "tenant_id": 1, "subject_id": "untrusted"})
        if body_identity.status_code != 422:
            raise RuntimeError("CLIENT_BODY_IDENTITY_OVERRIDE_NOT_REJECTED")
        if tenant1_kb:
            cross_read = client.request("GET", f"/api/v1/knowledge-bases/{tenant1_kb}/documents")
            cross_session = client.request("POST", "/api/v1/sessions", json={
                "title": "M4 denied cross-tenant scope",
                "knowledge_base_scope": [tenant1_kb], "document_scope": []})
            if cross_read.status_code not in (403, 404) or cross_session.status_code not in (403, 404):
                raise RuntimeError("CROSS_TENANT_SCOPE_NOT_REJECTED")

        # One normal session creation proves the API session scope is server-
        # bound to tenant 332. It intentionally submits no question or Provider call.
        created = client.create_session(knowledge_base_scope=[first_kb], document_scope=[],
                                        title="M4 tenant332 zero-provider identity check")
        session_id = str(created.get("id", ""))
        if not session_id or created.get("knowledge_base_scope") != [first_kb] or \
                created.get("document_scope") != []:
            raise RuntimeError("TENANT332_SESSION_SCOPE_INVALID")
        cross_query = None
        if tenant1_kb:
            cross_query = client.request("POST", f"/api/v1/sessions/{session_id}/messages", json={
                "content": "M4 scope boundary check",
                "request_id": str(uuid.uuid4()), "mode": "quick",
                "knowledge_base_scope": [tenant1_kb], "document_scope": []})
            if cross_query.status_code != 403:
                raise RuntimeError("TENANT1_QUERY_SCOPE_NOT_REJECTED")
        sessions = client.get_json("/api/v1/sessions")
        if not any(str(item.get("id")) == session_id and item.get("knowledge_base_scope") == [first_kb]
                   for item in sessions):
            raise RuntimeError("TENANT332_SESSION_NOT_READABLE")
        if client.messages(session_id) != []:
            raise RuntimeError("TENANT332_NEW_SESSION_UNEXPECTED_MESSAGES")

        from scripts.local_runtime import environment, load_config
        cfg = load_config(args.config)
        env = environment(cfg, preflight["redis_run_id"])
        conn = psycopg.connect(env["WEKNORA_PORT_DATABASE_URL"], autocommit=True,
                              row_factory=dict_row, connect_timeout=5)
        try:
            conn.execute("BEGIN TRANSACTION READ ONLY")
            row = conn.execute(
                "SELECT tenant_id,subject_id,knowledge_base_scope,document_scope FROM sessions WHERE id=%s",
                (session_id,),
            ).fetchone()
            if not row or row["tenant_id"] != 332 or row["subject_id"] != _read_json(Path(args.p25_state))["subject"] or \
                    row["knowledge_base_scope"] != [first_kb] or row["document_scope"] != []:
                raise RuntimeError("TENANT332_SESSION_DATABASE_SCOPE_MISMATCH")
            current_local = conn.execute(
                "SELECT tenant_id,subject_id FROM port_local_principal WHERE slot='local'"
            ).fetchone()
            calls_after = conn.execute(
                "SELECT count(*) AS calls,count(*) FILTER (WHERE outcome='UNKNOWN') AS unknown "
                "FROM port_provider_calls WHERE tenant_id=332 AND subject_id=%s",
                (_read_json(Path(args.p25_state))["subject"],),
            ).fetchone()
            session_counts = conn.execute(
                "SELECT count(*) AS n FROM sessions WHERE tenant_id=332 AND subject_id=%s AND deleted_at IS NULL",
                (_read_json(Path(args.p25_state))["subject"],),
            ).fetchone()["n"]
            conn.execute("ROLLBACK")
        finally:
            conn.close()
        if current_local["tenant_id"] != 1 or \
                _subject_fingerprint(current_local["subject_id"]) != preflight["daily_principal_subject_fingerprint"]:
            raise RuntimeError("DAILY_LOCAL_PRINCIPAL_CHANGED")
        if calls_after["calls"] != preflight["provider_calls_before"] or \
                calls_after["unknown"] != preflight["unknown_calls_before"]:
            raise RuntimeError("PROVIDER_LEDGER_CHANGED_DURING_ZERO_CALL_TEST")
        if session_counts != preflight["existing_subject_sessions"] + 1:
            raise RuntimeError("TENANT332_SESSION_WRITE_COUNT_MISMATCH")

        startup = _read_json(api_session.run_dir / "server-startup-summary.json")
        result = {
            "schema": "rag-m4-tenant332-api/v1",
            "status": "IDENTITY_API_READY_ZERO_PROVIDER",
            "started_at": _utc_now(),
            "api": {"bind_host": "127.0.0.1", "port": api_session.port,
                    "redis_prefix": api_session.redis_prefix,
                    "run_dir": str(api_session.run_dir),
                    "server_log": str(api_session.run_dir / "server.log"),
                    "process_state": str(api_session.run_dir / "process-state.json"),
                    "bearer_token_ttl_seconds": TOKEN_TTL_SECONDS,
                    "token_persisted": False, "worker_started": False,
                    "process_id": api_session.process_id, "stopped_after_verification": True},
            "identity": preflight,
            "startup_model_verification": startup,
            "http_checks": {
                "missing_token": no_token.status_code,
                "invalid_token": bad_token.status_code,
                "sse_replay_missing_token": sse_no_token.status_code,
                "tenant332_kbs_visible": len(TARGET_KBS),
                "target_documents_visible": sum(len(x) for x in api_documents.values()),
                "model_config_statuses": {name: public_roles[name].get("credential_status")
                                           for name in ("embedding", "rerank", "cheap", "expensive")},
                "tenant1_kb_read_status": cross_read.status_code if tenant1_kb else None,
                "client_body_identity_override_status": body_identity.status_code,
                "tenant1_session_scope_status": cross_session.status_code if tenant1_kb else None,
                "tenant1_query_scope_status": cross_query.status_code if cross_query else None,
                "created_session_id": session_id,
                "created_session_tenant_id": row["tenant_id"],
                "created_session_subject_fingerprint": _subject_fingerprint(row["subject_id"]),
                "provider_calls_after": calls_after["calls"],
                "unknown_after": calls_after["unknown"],
            },
            "document_api_statuses": api_documents,
            "model_configuration_values_emitted": False,
            "real_m4_questions_run": 0,
        }
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    verify.add_argument("--p25-state", type=Path, default=DEFAULT_P25_STATE)
    verify.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    verify.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    serve = sub.add_parser("_serve")
    serve.add_argument("--config", type=Path, required=True)
    serve.add_argument("--p25-state", type=Path, required=True)
    serve.add_argument("--redis-prefix", required=True)
    serve.add_argument("--port", type=int, required=True)
    serve.add_argument("--expires-at", required=True)
    serve.add_argument("--startup-summary", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "_serve":
        # The parent passes a fully composed, identity-checked environment.
        # Do not rebuild it from daily local.json here: that would lose the
        # isolated Redis/storage overrides and could change the egress mode.
        _serve(args)
        return 0
    try:
        result = _verify(args)
        path = Path(result["api"]["run_dir"]) / "identity-api-verification.json"
        _write_json(path, result)
        print(json.dumps({"status": result["status"], "report": str(path),
                          "tenant_id": result["identity"]["tenant_id"],
                          "subject_fingerprint": result["identity"]["subject_fingerprint"],
                          "visible_kbs": result["http_checks"]["tenant332_kbs_visible"],
                          "provider_calls": 0, "unknown": 0}, ensure_ascii=False))
        return 0
    except Exception as exc:
        # Deliberately omit exception text and locals: they may include a URL or
        # filesystem detail. Logs and machine results never contain credentials.
        code = str(exc) if isinstance(exc, RuntimeError) and \
            __import__("re").fullmatch(r"[A-Z][A-Z0-9_]{0,79}", str(exc)) else type(exc).__name__
        print("M4_TENANT332_API_VERIFY_FAILED:" + code, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

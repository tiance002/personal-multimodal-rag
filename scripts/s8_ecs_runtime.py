"""Private S8 Linux entrypoint over the formal API/Worker, never a cloud grant."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class RuntimeConfigurationError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise RuntimeConfigurationError(code)


def secret_file(path):
    value = Path(path).read_text(encoding="utf-8").strip()
    require(bool(value) and "\x00" not in value and "\n" not in value and "\r" not in value,
            "S8_SECRET_FILE_INVALID")
    return value


def trusted_principal(path):
    from backend.weknora_port.storage.history import LocalPrincipal
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    subject = value.get("subject_id", "")
    require(value.get("tenant_id") == 332 and isinstance(subject, str)
            and subject.startswith("p8-real-") and len(subject) > 20, "S8_VERIFIED_TENANT332_REQUIRED")
    actual = hashlib.sha256(subject.encode()).hexdigest()
    require(hmac.compare_digest(actual, value.get("subject_sha256", "")), "S8_SUBJECT_FINGERPRINT_MISMATCH")
    return LocalPrincipal(332, subject)


def runtime_environment(environment=None):
    """Read private DB password without placing it in Compose output or logs."""
    env = dict(os.environ if environment is None else environment)
    require(env.get("WEKNORA_PORT_DATABASE_NAME", "").startswith("weknora_s8"), "S8_DATABASE_NAMESPACE_REQUIRED")
    require(env.get("WEKNORA_PORT_DATABASE_SYSTEM_ID") not in (None, "", "0"), "S8_OBSERVED_POSTGRES_ID_REQUIRED")
    require(env.get("WEKNORA_PORT_REDIS_RUN_ID") not in (None, "", "0" * 40), "S8_OBSERVED_REDIS_ID_REQUIRED")
    require(env.get("WEKNORA_PORT_REDIS_PREFIX", "").startswith("weknora-python-s8:"), "S8_REDIS_NAMESPACE_REQUIRED")
    require(env.get("WEKNORA_PORT_GRAPH_ENABLED", "false").lower() == "false", "S8_OVERNIGHT_EXPERIMENTS_MUST_BE_OFF")
    env["WEKNORA_PORT_GRAPH_ENABLED"] = "false"
    top1 = env.get("WEKNORA_PORT_RERANK_TOP1_FLOORLESS", "true")
    require(isinstance(top1, str) and top1.lower() in ("true", "false"), "RERANK_TOP1_SETTING_INVALID")
    env["WEKNORA_PORT_RERANK_TOP1_FLOORLESS"] = top1.lower()
    password = secret_file(env.get("S8_DB_PASSWORD_FILE", "/run/secrets/database_password"))
    host = env.get("S8_DB_HOST", "db")
    require(bool(re.fullmatch(r"[a-zA-Z0-9_.-]+", host)), "S8_DATABASE_HOST_INVALID")
    port = int(env.get("S8_DB_PORT", "5432"))
    require(1 <= port <= 65535, "S8_DATABASE_PORT_INVALID")
    user = env.get("S8_DB_USER", "weknora")
    env["WEKNORA_PORT_DATABASE_URL"] = (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/"
        + env["WEKNORA_PORT_DATABASE_NAME"])
    env.setdefault("WEKNORA_PORT_CLOUD_ALLOWED", "false")
    env.setdefault("WEKNORA_PORT_ROUTER_MODE", "DYNAMIC")
    require(env["WEKNORA_PORT_ROUTER_MODE"] == "DYNAMIC", "S8_ORIGINAL_DYNAMIC_REQUIRED")
    return env


def api_token(environment=None):
    env = os.environ if environment is None else environment
    token = secret_file(env.get("S8_API_TOKEN_FILE", "/run/secrets/api_token"))
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token)), "S8_PRIVATE_API_TOKEN_INVALID")
    return token


class PrivateApiGuard:
    """All HTTP routes, including assets/health/SSE recovery, require a bearer."""
    def __init__(self, app, token, readiness=None):
        self.app, self.token, self.readiness = app, token.encode("ascii"), readiness

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        values = [v for k, v in scope.get("headers", []) if k.lower() == b"authorization"]
        valid = (len(values) == 1 and values[0].startswith(b"Bearer ")
                 and hmac.compare_digest(values[0][7:], self.token))
        if not valid:
            await self.response(send, 401, {"error": {"code": "S8_PRIVATE_API_TOKEN_REQUIRED"}})
            return
        if scope["path"] == "/readyz" and self.readiness is not None:
            status, body = await self.readiness()
            await self.response(send, status, body)
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def response(send, status, value):
        body = json.dumps(value, separators=(",", ":")).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


async def verify_runtime(runtime, settings):
    """Read existing identity/roles strictly. No bootstrap or model-row writes."""
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore
    principal = runtime.principal
    async with runtime.leases.lease() as guard:
        row = await (await guard.connection.execute(
            "SELECT id FROM tenants WHERE id=%s AND deleted_at IS NULL", (principal.tenant_id,))).fetchone()
        require(row is not None, "S8_EXISTING_TENANT_REQUIRED")
        _, roles = await PostgresModelRoleStore(guard, principal, runtime.catalogue, cipher=runtime.cipher).read()
    bindings = {}
    for role in roles:
        if role.role not in ("cheap", "expensive", "embedding", "rerank"):
            continue
        require(bool(role.provider and role.model), "S8_ARCHIVED_MODEL_ROLE_MISSING")
        model_id = runtime.models._identity(role)[0]
        resolved = await runtime.models.resolve(model_id)
        require((resolved.role, resolved.provider, resolved.model) == (role.role, role.provider, role.model),
                "S8_ARCHIVED_MODEL_BINDING_MISMATCH")
        bindings[role.role] = {"model_id": model_id, "provider": role.provider, "model": role.model}
    require(set(bindings) == {"cheap", "expensive", "embedding", "rerank"}, "S8_FOUR_ARCHIVED_ROLES_REQUIRED")
    return {"tenant_id": principal.tenant_id, "subject_sha256": hashlib.sha256(principal.subject_id.encode()).hexdigest(),
            "database": settings.database, "postgres_system_id": settings.system_id,
            "redis_run_id": settings.redis_run_id, "redis_prefix": settings.redis_prefix,
            "storage_root": str(settings.storage), "models": bindings,
            "cloud_allowed": runtime.cloud_allowed, "graph_enabled": runtime.graph_enabled,
            "rerank_top1_floorless": runtime.conversation.rerank_top1_floorless,
            "worker_in_api": False, "model_requests_at_startup": 0}


def settings_and_factory(environment=None, *, assemble_factory=None):
    from backend.weknora_port.development import DevelopmentSettings, assemble
    env = runtime_environment(environment)
    settings = DevelopmentSettings.from_environment(env)
    principal = trusted_principal(env.get("S8_PRINCIPAL_FILE", "/run/secrets/principal.json"))
    box = {}

    @asynccontextmanager
    async def factory(active):
        try:
            async with (assemble_factory or assemble)(active, principal=principal) as runtime:
                identity = await verify_runtime(runtime, active)
                box["runtime"] = runtime
                box["identity"] = identity
                logs = Path(env.get("S8_LOG_DIR", "/data/logs"))
                logs.mkdir(parents=True, exist_ok=True)
                report = logs / f"{env.get('S8_COMPONENT', 'api')}-startup-{time.time_ns()}.json"
                report.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
                try:
                    yield runtime
                finally:
                    box.pop("runtime", None)
        except RuntimeConfigurationError:
            raise
        except Exception as exc:
            # DB/transport exception messages may include connection secrets.
            raise RuntimeConfigurationError("S8_RUNTIME_ASSEMBLY_FAILED_" + type(exc).__name__) from None

    return settings, factory, box


def create_application(environment=None, *, assemble_factory=None):
    from backend.weknora_port.entrypoint import create_app
    env = os.environ if environment is None else environment
    settings, factory, box = settings_and_factory(env, assemble_factory=assemble_factory)

    async def readiness():
        runtime = box.get("runtime")
        if runtime is None:
            return 503, {"error": {"code": "S8_RUNTIME_NOT_STARTED"}}
        async def probe():
            async with runtime.leases.lease() as guard:
                await guard.connection.execute("SELECT 1")
            require((await runtime.store.client.info("server"))["run_id"] == settings.redis_run_id,
                    "S8_REDIS_IDENTITY_MISMATCH")
        try:
            await asyncio.wait_for(probe(), 5)
        except Exception:
            return 503, {"error": {"code": "S8_RESOURCES_NOT_READY"}}
        return 200, {"implementation": "weknora-python-port", "status": "ready", **box["identity"]}

    return PrivateApiGuard(create_app(settings=settings, runtime_factory=factory), api_token(env), readiness)


def check(base_url, environment=None):
    require(base_url in ("http://127.0.0.1:8000", "http://127.0.0.1:18800"), "S8_LOOPBACK_HEALTH_TARGET_REQUIRED")
    request = urllib.request.Request(base_url + "/readyz", headers={"Authorization": "Bearer " + api_token(environment)})
    with urllib.request.urlopen(request, timeout=8) as response:
        value = json.load(response)
        require(response.status == 200 and value.get("implementation") == "weknora-python-port"
                and value.get("tenant_id") == 332, "S8_READINESS_FAILED")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("api", "worker", "check"))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.mode == "check":
        check(args.base_url)
        return 0
    if args.mode == "api":
        import uvicorn
        # Container-only address; Compose publishes only the host loopback.
        uvicorn.run(create_application(), host="0.0.0.0", port=args.port, workers=1,
                    log_level="warning", access_log=False)
    else:
        from backend.weknora_port.worker import serve
        settings, factory, _ = settings_and_factory()
        asyncio.run(serve(settings, args.once, runtime_factory=factory))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception,) as exc:
        code = str(exc) if isinstance(exc, RuntimeConfigurationError) else "S8_RUNTIME_FAILED_" + type(exc).__name__
        print(json.dumps({"status": "BLOCKED", "error_code": code}), file=sys.stderr)
        raise SystemExit(1)

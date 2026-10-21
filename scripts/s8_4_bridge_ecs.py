"""Fresh bounded S8.4-A diagnostic over existing ECS Registry/Gate/ledger.

Imports do not connect or send. Prepare is zero-send; run consumes only
the explicitly pinned fresh grant. No ordinary API, final generation or ingest.
"""
from __future__ import annotations
import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

MAX_CALLS = 8
MAX_TOKENS = 30000
SCHEMA = "s84a-bridge-input/v1"
DATABASE = "weknora_s8"
SYSTEM_ID = "7698420805889187877"
TENANT = 332
ROOT_PREFIX = "/opt/rag-s8/state/s8-4-bridge-"
EXECUTION_ROOT = "/opt/rag-s8/state/executions"


def require(condition, code):
    if not condition:
        raise ValueError(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode("utf-8")).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=str) + "\n")
        output.flush()
        os.fsync(output.fileno())


def safe_write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    write_new(temporary, value)
    os.replace(temporary, path)


def validate_payload(payload):
    require(isinstance(payload, dict) and payload.get("schema") == SCHEMA, "S84A_INPUT_SCHEMA_INVALID")
    cases = payload.get("cases")
    require(isinstance(cases, list) and 3 <= len(cases) <= 8, "S84A_CASE_COUNT_INVALID")
    ids = []
    forbidden = {"gold", "expected_answer", "answer", "answer_points", "expected_chunk_ids"}
    for case in cases:
        require(isinstance(case, dict) and not forbidden.intersection(case), "S84A_GOLD_IN_INPUT")
        cid = case.get("case_id")
        require(isinstance(cid, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", cid), "S84A_CASE_ID_INVALID")
        ids.append(cid)
        require(isinstance(case.get("query"), str) and bool(case["query"]), "S84A_QUESTION_REQUIRED")
        require(isinstance(case.get("rewrite_query"), str), "S84A_REWRITE_REQUIRED")
        kbs = case.get("knowledge_base_ids")
        docs = case.get("document_ids", [])
        require(isinstance(kbs, list) and kbs and all(isinstance(v, str) and v for v in kbs), "S84A_SCOPE_REQUIRED")
        require(len(set(kbs)) == len(kbs) and isinstance(docs, list) and all(isinstance(v, str) and v for v in docs), "S84A_SCOPE_INVALID")
        for field in ("search_results", "rerank_results", "merge_results", "evidence_trace"):
            require(isinstance(case.get(field), list), "S84A_FROZEN_STAGE_REQUIRED")
        require(len(case["search_results"]) <= 30 and case.get("rerank_threshold", .3) == .3, "S84A_FIRST_RERANK_CHANGED")
    require(len(ids) == len(set(ids)), "S84A_DUPLICATE_CASE")
    return cases


def validate_owner(owner, payload_bytes):
    require(isinstance(owner, dict) and owner.get("approval_source") == "Owner explicit current messages",
            "S84A_CURRENT_OWNER_APPROVAL_REQUIRED")
    require(type(owner.get("max_calls")) is int and owner["max_calls"] == MAX_CALLS and
            type(owner.get("max_tokens")) is int and owner["max_tokens"] == MAX_TOKENS,
            "S84A_APPROVED_BOUNDS_REQUIRED")
    require(owner.get("requests_sha256") == sha(payload_bytes), "S84A_APPROVED_INPUT_CHANGED")
    stage = owner.get("stage_id")
    require(isinstance(stage, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,96}", stage), "S84A_FRESH_STAGE_REQUIRED")
    return "s84a-" + stage + "/"


def request_plan(cases, prefix):
    """Exact identities, excluding every retry or final-generation suffix."""
    from backend.weknora_port.retrieval.bridge_rescue import _location_question
    potential = [c for c in cases if _location_question(c["query"]) and c["merge_results"]]
    require(len(potential) * 2 <= MAX_CALLS, "S84A_PLANNED_CALL_BOUND_EXCEEDED")
    return {prefix + c["case_id"] + "/" + suffix: capability
            for c in potential
            for suffix, capability in (("bridge-extract", "chat"), ("bridge-rerank", "rerank"))}


def source_identity(row):
    return {key: row[key] for key in
            ("id", "content", "knowledge_id", "knowledge_base_id", "chunk_type",
             "chunk_index", "start_at", "end_at")}


def source_links_match(left, right):
    return all(key not in left or key not in right or (left.get(key) or "") == (right.get(key) or "")
               for key in ("pre_chunk_id", "next_chunk_id", "parent_chunk_id"))


def verify_source_pair(frozen, actual):
    """Ignore generated descriptions; compare original identity/body/spans."""
    a = asdict(actual) if not isinstance(actual, dict) else actual
    require(source_identity(frozen) == source_identity(a) and source_links_match(frozen, a), "S84A_CURRENT_SOURCE_BODY_OR_IDENTITY_CHANGED")
    old_version = (frozen.get("metadata") or {}).get("source_version")
    require(bool(old_version) and (a.get("metadata") or {}).get("source_version") == old_version and
            (a.get("chunk_metadata") or {}).get("source_version") == old_version, "S84A_CURRENT_SOURCE_VERSION_CHANGED")
    require(a.get("is_enabled") is True and not a.get("content_rewritten") and not a.get("content_revision"),
            "S84A_CURRENT_SOURCE_NOT_ORIGINAL")
    require(type(a["start_at"]) is int and type(a["end_at"]) is int and
            a["start_at"] >= 0 and a["end_at"] - a["start_at"] == len(a["content"]),
            "S84A_CURRENT_SOURCE_UNICODE_LOCATOR_INVALID")
    return {"chunk_id": a["id"], "knowledge_base_id": a["knowledge_base_id"],
            "document_id": a["knowledge_id"], "source_version": old_version,
            "start_at": a["start_at"], "end_at": a["end_at"], "sha256": sha(a["content"])}


def runtime_settings(stage, *, cloud=False, ledger=None):
    from scripts.s8_ecs_runtime import runtime_environment, trusted_principal
    from backend.weknora_port.development import DevelopmentSettings
    env = runtime_environment()
    env["WEKNORA_PORT_CLOUD_ALLOWED"] = str(cloud).lower()
    env.pop("WEKNORA_PORT_EXECUTION_FILE", None)
    env.pop("WEKNORA_PORT_EVIDENCE_DIR", None)
    env["WEKNORA_PORT_REDIS_PREFIX"] = "weknora-python-s8:s84a-" + stage
    if ledger is not None:
        env["WEKNORA_PORT_EXECUTION_FILE"] = str(ledger)
        os.environ["WEKNORA_PORT_EXECUTION_ROOT"] = EXECUTION_ROOT
    principal = trusted_principal(env.get("S8_PRINCIPAL_FILE", "/run/secrets/principal.json"))
    settings = DevelopmentSettings.from_environment(env)
    require(settings.database == DATABASE and settings.system_id == SYSTEM_ID and
            principal.tenant_id == TENANT, "S84A_ECS_RESOURCE_IDENTITY_MISMATCH")
    storage_marker_identity(settings)
    return settings, principal


def storage_marker_identity(settings):
    """Require the already-owned storage marker; never let diagnostic assembly create it."""
    marker = settings.storage / ".weknora-python-storage.json"
    try:
        raw = marker.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError("S84A_EXISTING_STORAGE_MARKER_REQUIRED") from None
    require(value == {"database": DATABASE, "system_id": SYSTEM_ID},
            "S84A_STORAGE_IDENTITY_MISMATCH")
    return {"path": str(marker), "sha256": sha(raw), "database": DATABASE, "system_id": SYSTEM_ID}


async def history_snapshot(app, prefix=""):
    where = "WHERE request_id NOT LIKE %s" if prefix else ""
    rows = await app._rows(
        "SELECT count(*) AS calls,count(*) FILTER(WHERE outcome='UNKNOWN') AS unknown,"
        "count(*) FILTER(WHERE finished_at IS NULL AND outcome IS DISTINCT FROM 'UNKNOWN') AS unfinished,"
        "md5(string_agg(md5(row_to_json(p)::text),'' ORDER BY call_id)) AS fingerprints "
        "FROM port_provider_calls p " + where, (prefix + "%",) if prefix else ())
    data = await app._rows(
        "SELECT (SELECT md5(string_agg(md5(row_to_json(c)::text),'' ORDER BY tenant_id,subject_id)) "
        "FROM port_model_configuration c) AS role_hash,"
        "(SELECT md5(string_agg(md5(row_to_json(m)::text),'' ORDER BY id)) FROM models m) AS model_hash,"
        "(SELECT count(*) FROM knowledges) AS documents,(SELECT count(*) FROM chunks) AS chunks,"
        "(SELECT count(*) FROM embeddings) AS vectors")
    return {"provider": rows[0], "data": data[0]}


def case_context(case, principal, prefix):
    from backend.weknora_port.models.types import CallContext, TrustedScope
    return CallContext(prefix + case["case_id"],
        TrustedScope(principal.subject_id, tuple(case["knowledge_base_ids"]), tuple(case.get("document_ids", []))),
        deadline=asyncio.get_running_loop().time() + 120, deadline_kind="run")


async def current_source_checks(app, registry, cases, prefix, principal):
    from backend.weknora_port.runtime import LeasedKnowledgeRepository
    repo = LeasedKnowledgeRepository(app, registry)
    checked = []
    for case in cases:
        context = case_context(case, principal, prefix)
        frozen = {}
        for row in case.get("source_rows", case["search_results"] + case["merge_results"]):
            if row is not None and row["chunk_type"] == "text":
                prior = frozen.setdefault(row["id"], row)
                require(source_identity(prior) == source_identity(row) and source_links_match(prior, row), "S84A_FROZEN_SOURCE_CONFLICT")
        loaded = dict(await repo.get_current_chunks(tuple(frozen), context))
        require(set(loaded) == set(frozen), "S84A_CURRENT_SOURCE_SCOPE_OR_VERSION_DENIED")
        proof = [verify_source_pair(frozen[key], loaded[key]) for key in sorted(frozen)]
        checked.append({"case_id": case["case_id"], "sources": proof})
    return checked


async def prepare(root, *, write=True):
    from backend.weknora_port.development import assemble
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore
    payload_bytes = (root / "payload/inputs.json").read_bytes()
    payload = read(root / "payload/inputs.json")
    owner = read(root / "payload/owner-authorization.json")
    cases = validate_payload(payload)
    prefix = validate_owner(owner, payload_bytes)
    plan = request_plan(cases, prefix)
    settings, principal = runtime_settings(owner["stage_id"])
    roles_out = {}
    async with assemble(settings, principal=principal) as app:
        identity = await app.runtime_identity(principal)
        before = await history_snapshot(app)
        require(before["provider"]["unfinished"] == 0, "S84A_UNFINISHED_PROVIDER_OPERATION_STOP")
        async with app.leases.lease() as guard:
            revision, roles = await PostgresModelRoleStore(
                guard, principal, app.catalogue, cipher=app.cipher).read()
        for name, capability in (("cheap", "chat"), ("rerank", "rerank")):
            role = next((r for r in roles if r.role == name), None)
            require(role is not None, "S84A_FORMAL_ROLE_REQUIRED")
            mid = app.models._identity(role)[0]
            loaded = await app.models.resolve(mid)
            require(loaded.model == role.model and loaded.provider == role.provider,
                    "S84A_REGISTRY_ROLE_MISMATCH")
            roles_out[name] = {"provider": role.provider, "model": role.model, "model_id": mid, "capability": capability}
        registry = app.registry_for_context()
        try:
            sources = await current_source_checks(app, registry, cases, prefix, principal)
        finally:
            await registry.close()
        require(await history_snapshot(app) == before, "S84A_ZERO_SEND_MUTATION")
    result = {"status": "S84A_READY_ZERO_SEND", "source_requests_sha256": sha(payload_bytes),
              "stage_prefix": prefix, "identity": identity, "subject_sha256": sha(principal.subject_id),
              "storage_marker": storage_marker_identity(settings),
              "role_revision": revision, "registry_roles": roles_out, "sources": sources,
              "exact_request_plan": plan, "max_calls": MAX_CALLS, "max_tokens": MAX_TOKENS,
              "before": before, "new_provider_calls": 0, "actual_tokens_added": 0}
    if write:
        write_new(root / "preflight.json", result)
    return result


def validate_settlements(rows, entries, plan, roles):
    require(len(rows) == len(entries) <= MAX_CALLS, "S84A_DUAL_LEDGER_COUNT_MISMATCH")
    pg = {}
    for row in rows:
        identity = (row["request_id"], row["capability"])
        require(identity not in pg, "S84A_DUPLICATE_PROVIDER_OPERATION")
        pg[identity] = row
    execution = {}
    for entry in entries:
        identity = (entry["request_id"], entry["capability"])
        require(identity not in execution, "S84A_DUPLICATE_EXECUTION_OPERATION")
        execution[identity] = entry
    require(set(pg) == set(execution), "S84A_DUAL_LEDGER_KEYS_MISMATCH")
    totals = {"chat": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
              "rerank": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}
    for entry in execution.values():
        rid, capability = entry["request_id"], entry["capability"]
        require(plan.get(rid) == capability and capability in totals, "S84A_UNAPPROVED_OPERATION")
        require(entry["status"] == "SETTLED" and entry.get("outcome") == "RESPONSE_RECEIVED",
                "S84A_NEW_UNKNOWN_STOP")
        row = pg.get((rid, capability))
        require(row is not None and row["outcome"] == "RESPONSE_RECEIVED" and row.get("finished_at"),
                "S84A_PROVIDER_SETTLEMENT_STOP")
        role = roles["cheap" if capability == "chat" else "rerank"]
        require(all(row[key] == entry[key] == role[key] for key in ("provider", "model", "model_id")),
                "S84A_SETTLEMENT_MODEL_MISMATCH")
        require(row["wire_sha256"] == entry["wire_sha256"], "S84A_SETTLEMENT_WIRE_MISMATCH")
        usage, local = row.get("usage"), entry.get("usage")
        require(isinstance(usage, dict) and isinstance(local, dict) and
                usage.get("status") == local.get("status") == "REPORTED", "S84A_USAGE_UNKNOWN_STOP")
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            require(type(usage.get(key)) is int and usage[key] >= 0 and usage[key] == local.get(key),
                    "S84A_USAGE_MISMATCH")
        require(entry["held_tokens"] == usage["total_tokens"], "S84A_HELD_USAGE_MISMATCH")
        totals[capability]["calls"] += 1
        totals[capability]["input_tokens"] += usage["prompt_tokens"]
        totals[capability]["output_tokens"] += usage["completion_tokens"]
        totals[capability]["total_tokens"] += usage["total_tokens"]
    require(sum(value["total_tokens"] for value in totals.values()) <= MAX_TOKENS, "S84A_ACTUAL_TOKEN_BOUND_EXCEEDED")
    return totals


class RequestFence:
    """Restrict transport calls while retaining the formal transport and gate."""
    def __init__(self, plan, roles, *, audit=None):
        require(isinstance(plan, dict) and 0 < len(plan) <= MAX_CALLS and
                all(value in ("chat", "rerank") for value in plan.values()), "S84A_FENCE_PLAN_INVALID")
        self.plan, self.roles, self.audit = dict(plan), roles, audit
        self.seen = set()
        self.bound_cases = {}

    def bind_case(self, case, context, state):
        sources = {}
        for row in case.get("source_rows", case["search_results"] + case["merge_results"]):
            if row is not None and row["chunk_type"] == "text":
                sources[row["id"]] = row
        for suffix in ("bridge-extract", "bridge-rerank"):
            self.bound_cases[context.request_id + "/" + suffix] = (case, context.scope, state, sources)

    def validate(self, config, body, context, capability):
        rid = context.request_id
        require(self.plan.get(rid) == capability and rid not in self.seen and len(self.seen) < MAX_CALLS, "S84A_EXACT_OPERATION_DENIED")
        role = self.roles["cheap" if capability == "chat" else "rerank"]
        require(all(getattr(config, key) == role[key] for key in ("provider", "model", "model_id")),
                "S84A_WIRE_ROLE_MISMATCH")
        require(isinstance(body, dict), "S84A_WIRE_INVALID")
        bound = self.bound_cases.get(rid)
        if bound is not None:
            require(context.scope == bound[1] and not context.required_origin_kbs,
                    "S84A_WIRE_SCOPE_CHANGED")
        if capability == "chat":
            from backend.weknora_port.retrieval.bridge_rescue import SYSTEM
            messages = body.get("messages")
            require(isinstance(messages, list) and len(messages) == 2 and
                    messages[0].get("role") == "system" and messages[0].get("content") == SYSTEM and
                    messages[1].get("role") == "user" and isinstance(messages[1].get("content"), str),
                    "S84A_EXTRACTION_PROMPT_MISMATCH")
            content = messages[1]["content"]
            # Existing Adapter appends its explicit format hint after the JSON
            # input. Accept exactly that pinned transform, never arbitrary tail.
            format_hint = "\nUse this JSON schema: json"
            if content.endswith(format_hint):
                require(body.get("response_format") == {"type": "json_object"},
                        "S84A_EXTRACTION_FORMAT_MISMATCH")
                content = content[:-len(format_hint)]
            data = json.loads(content)
            require(set(data) == {"question", "windows"} and isinstance(data["question"], str) and
                    isinstance(data["windows"], list) and data["windows"] and
                    sum(len(w["text"]) for w in data["windows"]) <= 2400, "S84A_EXTRACTION_INPUT_INVALID")
            if bound is not None:
                case, _, _, sources = bound
                require(data["question"] == case["query"], "S84A_WIRE_QUESTION_CHANGED")
                main_ids, previous_ids = set(), set()
                for window in data["windows"]:
                    require(isinstance(window, dict) and set(window) ==
                            {"source_chunk_id", "start", "end", "text", "role"}, "S84A_WINDOW_SCHEMA_INVALID")
                    source = sources.get(window["source_chunk_id"])
                    start, end = window["start"], window["end"]
                    require(source is not None and type(start) is int and type(end) is int and
                            0 <= start < end <= len(source["content"]) and
                            source["content"][start:end] == window["text"], "S84A_WINDOW_SOURCE_CHANGED")
                    require(window["role"] in ("main", "preceding"), "S84A_WINDOW_ROLE_INVALID")
                    (main_ids if window["role"] == "main" else previous_ids).add(source["id"])
                require(1 <= len(main_ids) <= 2 and len(previous_ids) <= 1 and
                        main_ids.isdisjoint(previous_ids), "S84A_WINDOW_SOURCE_BOUND_EXCEEDED")
            require(body.get("max_tokens", body.get("max_completion_tokens")) == 256 and body.get("temperature") == 0 and
                    body.get("stream", False) is False and body.get("enable_thinking", False) is False,
                    "S84A_EXTRACTION_OPTIONS_CHANGED")
            require(all(isinstance(m["content"], str) for m in messages) and
                    not any(k in body for k in ("images", "audio", "tools")), "S84A_TEXT_EXTRACTION_REQUIRED")
        elif capability == "rerank":
            require(isinstance(body.get("query"), str) and 0 < len(body["query"]) <= 192 and
                    isinstance(body.get("documents"), list) and 1 <= len(body["documents"]) <= 8 and
                    all(isinstance(d, str) and d for d in body["documents"]), "S84A_RERANK_INPUT_INVALID")
            if bound is not None:
                _, _, state, sources = bound
                authorized = next((row for row in reversed(state.bridge_trace)
                                   if row.get("decision") == "SECOND_RERANK_INPUT"), None)
                require(authorized is not None and body["query"] == authorized["q1"] and
                        body["documents"] == [sources[cid]["content"] for cid in authorized["candidate_ids"]],
                        "S84A_PROGRAM_DERIVED_RERANK_CHANGED")
        else:
            raise ValueError("S84A_CAPABILITY_DENIED")
        self.seen.add(rid)

    def install(self, model):
        transport = model.transport
        existing = getattr(transport, "_s84a_fence", None)
        require(existing in (None, self), "S84A_FENCE_IDENTITY_CHANGED")
        if existing is self:
            return model
        original = transport.open
        fence = self

        @asynccontextmanager
        async def opened(config, path, body, context, **kwargs):
            try:
                fence.validate(config, body, context, kwargs.get("capability"))
            except (ValueError, KeyError, TypeError) as error:
                # Optional rescue must not swallow a diagnostic send denial.
                raise PermissionError(str(error)) from None
            failure = None
            try:
                async with original(config, path, body, context, **kwargs) as handle:
                    yield handle
            except BaseException as error:
                failure = error
                raise
            finally:
                if fence.audit is not None:
                    try:
                        await asyncio.shield(fence.audit())
                    except Exception:
                        from backend.weknora_port.models.types import CallCancelled, RunDeadlineExceeded
                        if not isinstance(failure, (CallCancelled, RunDeadlineExceeded, asyncio.CancelledError)):
                            raise
        transport.open, transport._s84a_fence = opened, self
        return model


class FencedRegistry:
    def __init__(self, registry, fence):
        self.registry, self.fence = registry, fence

    async def chat(self, model_id, context):
        if model_id != self.fence.roles["cheap"]["model_id"]:
            raise PermissionError("S84A_CHEAP_ROLE_ID_REQUIRED")
        return self.fence.install(await self.registry.chat(model_id, context))

    async def rerank(self, model_id, context):
        if model_id != self.fence.roles["rerank"]["model_id"]:
            raise PermissionError("S84A_RERANK_ROLE_ID_REQUIRED")
        return self.fence.install(await self.registry.rerank(model_id, context))


def build_state(case, roles):
    from backend.weknora_port.retrieval.pipeline import PipelineState
    from backend.weknora_port.retrieval.search import SearchTarget
    from backend.weknora_port.retrieval.types import SearchResult
    return PipelineState(query=case["query"], rewrite_query=case["rewrite_query"],
        targets=tuple(SearchTarget(kb, "knowledge" if case.get("document_ids") else "knowledge_base",
                                   tuple(case.get("document_ids", []))) for kb in case["knowledge_base_ids"]),
        search_results=[SearchResult(**row) if row is not None else None for row in case["search_results"]],
        rerank_results=[SearchResult(**row) if row is not None else None for row in case["rerank_results"]],
        merge_results=[SearchResult(**row) if row is not None else None for row in case["merge_results"]],
        evidence_trace=case["evidence_trace"], rerank_threshold=.3,
        enable_rewrite=False, enable_expansion=False, bridge_rescue_mode="cheap_bridge_v1",
        bridge_cheap_model_id=roles["cheap"]["model_id"], rerank_model_id=roles["rerank"]["model_id"],
        context_template="{{contexts}}\n{{query}}")


def ledger_spec(principal, cases, pre):
    return {"status": "OPEN", "scope": "S8_4_A_CURRENT_OWNER", "tenant_id": principal.tenant_id,
            "subject_id": principal.subject_id,
            "allowed_kbs": sorted({kb for c in cases for kb in c["knowledge_base_ids"]}),
            "allowed_models": list(pre["registry_roles"].values()),
            "allowed_request_prefixes": sorted(pre["exact_request_plan"]),
            "max_calls": MAX_CALLS, "max_tokens": MAX_TOKENS, "capture_evidence": True, "entries": []}


def close_execution_data(data, *, success):
    if success:
        require(all(row["status"] == "SETTLED" for row in data["entries"]), "S84A_CANNOT_CLOSE_UNSETTLED")
    if data["status"] != "STOP":
        data["status"] = "CLOSED" if success else "STOP"


def close_ledger(path, *, success):
    from backend.weknora_port.execution import LocalExecution
    execution = LocalExecution(path)
    with execution._ledger() as data:
        close_execution_data(data, success=success)


async def provider_rows(app, principal, prefix):
    return await app._rows(
        "SELECT call_id,request_id,capability,provider,model,model_id,outcome,usage,wire_sha256,finished_at "
        "FROM port_provider_calls WHERE tenant_id=%s AND subject_id=%s AND request_id LIKE %s ORDER BY request_id",
        (principal.tenant_id, principal.subject_id, prefix + "%"))


def safe_error(error):
    code = getattr(error, "code", "")
    if isinstance(code, str) and re.fullmatch("[A-Z0-9_]+", code):
        return code
    if isinstance(error, (ValueError, PermissionError)) and re.fullmatch("[A-Z0-9_]+", str(error)):
        return str(error)
    return type(error).__name__


async def run(root):
    from backend.weknora_port.development import assemble
    from backend.weknora_port.runtime import LeasedKnowledgeRepository
    from backend.weknora_port.retrieval.bridge_rescue import rescue_bridge
    from backend.weknora_port.retrieval.context import into_chat_message
    from backend.weknora_port.retrieval.types import context_results
    pre = read(root / "preflight.json")
    payload = read(root / "payload/inputs.json")
    owner = read(root / "payload/owner-authorization.json")
    cases = validate_payload(payload)
    prefix = validate_owner(owner, (root / "payload/inputs.json").read_bytes())
    require(pre == await prepare(root, write=False), "S84A_PREFLIGHT_CHANGED_BEFORE_RUN")
    write_new(root / "run.claim", {"stage_prefix": prefix, "max_calls": MAX_CALLS,
                                 "max_tokens": MAX_TOKENS, "time": time.time()})
    execution_dir = Path(EXECUTION_ROOT) / ("s84a-" + owner["stage_id"])
    require(not execution_dir.exists(), "S84A_EXISTING_EXECUTION_NO_RESET")
    execution_dir.mkdir(parents=True, mode=0o700)
    ledger = execution_dir / "execution.json"
    _, principal = runtime_settings(owner["stage_id"])
    write_new(ledger, ledger_spec(principal, cases, pre))
    write_new(root / "bounded-execution.json", {"path": str(ledger), "stage_prefix": prefix,
        "max_calls": MAX_CALLS, "max_tokens": MAX_TOKENS, "exact_request_plan": pre["exact_request_plan"]})
    settings, principal = runtime_settings(owner["stage_id"], cloud=True, ledger=ledger)
    results, error, rows = [], None, []
    success = False
    totals = None
    after = sources_after = storage_after = None
    try:
        async with assemble(settings, principal=principal) as app:
            registry = app.registry_for_context()

            async def audit():
                current_rows = await provider_rows(app, principal, prefix)
                try:
                    validate_settlements(current_rows, read(ledger)["entries"], pre["exact_request_plan"], pre["registry_roles"])
                except ValueError as error:
                    raise PermissionError(str(error)) from None

            fence = RequestFence(pre["exact_request_plan"], pre["registry_roles"], audit=audit)
            repo = LeasedKnowledgeRepository(app, registry)
            try:
                for case in cases:
                    require(read(ledger)["status"] == "OPEN", "S84A_GLOBAL_STOP")
                    context = case_context(case, principal, prefix)
                    state = build_state(case, pre["registry_roles"])
                    fence.bind_case(case, context, state)
                    original = canonical({key: [asdict(row) if row is not None else None for row in getattr(state, key)]
                                          for key in ("search_results", "rerank_results", "merge_results")})
                    result = {"case_id": case["case_id"], "arm": case.get("arm"), "status": "RUNNING"}
                    started = time.monotonic()
                    try:
                        await rescue_bridge(state, context, repo, FencedRegistry(registry, fence))
                        require(original == canonical({key: [asdict(row) if row is not None else None for row in getattr(state, key)]
                                for key in ("search_results", "rerank_results", "merge_results")}), "S84A_NORMAL_PIPELINE_MUTATED")
                        finalized = await into_chat_message(state, context)
                        require(tuple(row.id for row in context_results(state)) == finalized.result_ids,
                                "S84A_CONTEXT_SOURCE_MAPPING_MISMATCH")
                        result.update(status="SUPPLEMENT_ACCEPTED" if state.rescue_evidence else "BRIDGE_UNVERIFIED_OR_NOT_APPLICABLE",
                            result_ids=list(finalized.result_ids), rescue_ids=[row.id for row in state.rescue_evidence],
                            finalized_context=asdict(finalized))
                    except BaseException as exc:
                        result.update(status="STOP", error=safe_error(exc))
                        raise
                    finally:
                        result.update(elapsed_seconds=time.monotonic() - started, trace=state.bridge_trace,
                                      q1_generated=any(row.get("decision") == "SECOND_RERANK_INPUT" for row in state.bridge_trace))
                        write_new(root / "cases" / (case["case_id"] + ".json"), result)
                        results.append(result)
                        safe_write(root / "results-checkpoint.json", {"status": "RUNNING", "cases": results})
                    await audit()
                    print(canonical({"case_id": case["case_id"], "status": result["status"],
                                     "rescue_ids": result.get("rescue_ids", [])}), flush=True)
                rows = await provider_rows(app, principal, prefix)
                totals = validate_settlements(rows, read(ledger)["entries"], pre["exact_request_plan"], pre["registry_roles"])
                success = True
            finally:
                await registry.close()
    except BaseException as exc:
        error = safe_error(exc)
    finally:
        close_ledger(ledger, success=success)
        zero, who = runtime_settings(owner["stage_id"])
        storage_after = storage_marker_identity(zero)
        async with assemble(zero, principal=who) as app:
            after = await history_snapshot(app, prefix)
            rows = await provider_rows(app, who, prefix)
            registry = app.registry_for_context()
            try:
                sources_after = await current_source_checks(app, registry, cases, prefix, who)
            finally:
                await registry.close()
        preserved = (after == pre["before"] and sources_after == pre["sources"] and
                     storage_after == pre["storage_marker"])
        entries = read(ledger)["entries"]
        reported = sum((e.get("usage") or {}).get("total_tokens", 0) for e in entries if e["status"] == "SETTLED")
        final = {"status": ("REAL_BRIDGE_FEASIBILITY_COMPLETE" if success and preserved else "REAL_BRIDGE_STOP"),
                 "error": error, "cases": results, "provider_rows": rows, "execution_entries": entries,
                 "usage_by_capability": totals, "provider_call_count": len(rows), "execution_claim_count": len(entries),
                 "actual_reported_tokens_subtotal": reported,
                 "provider_calls_by_capability": {cap: sum(r["capability"] == cap for r in rows)
                                                   for cap in ("chat", "rerank")},
                 "reported_tokens_by_capability": {cap: sum((e.get("usage") or {}).get("total_tokens", 0)
                     for e in entries if e["capability"] == cap and e["status"] == "SETTLED")
                     for cap in ("chat", "rerank")},
                 "unreported_calls_by_capability": {cap: sum(e["capability"] == cap and e["status"] != "SETTLED"
                     for e in entries) for cap in ("chat", "rerank")},
                 "all_actual_tokens": reported if all(e["status"] == "SETTLED" for e in entries) else None,
                 "new_unknown": sum(e["status"] != "SETTLED" for e in entries),
                 "old_history_roles_models_source_counts_preserved": preserved,
                 "before": pre["before"], "after_excluding_new": after, "sources_after": sources_after,
                 "storage_marker_before": pre["storage_marker"], "storage_marker_after": storage_after,
                 "normal_pipeline_not_run": True, "final_answer_generation_calls": 0, "embedding_calls": 0,
                 "production_configuration_changed": False, "deployment_performed": False,
                 "ledger_status": read(ledger)["status"],
                 "remaining_cases_not_run": len(cases) - len(results),
                 "bridge_semantics": "PARTIAL_OR_UNVERIFIED" if not any(r.get("rescue_ids") for r in results) else "REQUIRES_REVIEW"}
        write_new(root / "final.json", final)
        write_new(root / "authorization-closure.json", {"status": read(ledger)["status"],
            "actual_calls": len(entries), "actual_reported_tokens": reported,
            "unused_call_allowance_revoked": MAX_CALLS - len(entries),
            "unused_token_allowance_revoked": MAX_TOKENS - sum(e["held_tokens"] for e in entries),
            "historical_authorizations_reused": False})
        print(canonical({key: final[key] for key in ("status", "provider_call_count", "actual_reported_tokens_subtotal",
                                                    "new_unknown", "old_history_roles_models_source_counts_preserved")}), flush=True)
    return final["status"] == "REAL_BRIDGE_FEASIBILITY_COMPLETE"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "run"))
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    require(str(root).startswith(ROOT_PREFIX) and root.parent == Path("/opt/rag-s8/state"),
            "S84A_PRIVATE_DIAGNOSTIC_ROOT_REQUIRED")
    if args.mode == "prepare":
        result = asyncio.run(prepare(root))
        print(canonical({"status": result["status"], "planned_max_calls": len(result["exact_request_plan"]),
                         "max_calls": MAX_CALLS, "max_tokens": MAX_TOKENS}), flush=True)
    else:
        raise SystemExit(0 if asyncio.run(run(root)) else 2)


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        print(canonical({"status": "S84A_LOCAL_TOOL_STOP", "error": safe_error(error)}), flush=True)
        raise SystemExit(3)

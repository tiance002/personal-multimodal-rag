"""S8.1 ECS acceptance helpers. Inspection makes zero Provider requests.

Uses the existing ECS environment, formal assembly, archived Registry and request
builder. It never creates authorization, changes role rows, or changes defaults.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str).encode()).hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
        stream.write("\n")


def contract_probe(config, options, *, stream):
    """Same builder as ProviderModel; placeholder content is never sent.

    Proves only the output contract. Does not claim the unknown final prompt's
    wire bytes or total per-case Token sufficiency.
    """
    from backend.weknora_port.models.provider import ProviderModel
    from backend.weknora_port.models.types import Message, ProviderError
    from backend.weknora_port.models.output_contract import resolve_output_contract
    model = ProviderModel(config, None)
    body = model.build_request_body((Message("user", "S8.1 zero-send contract inspection"),),
                                   options, stream=stream)
    safe_fields = ("model", "max_tokens", "max_completion_tokens", "max_output_tokens",
        "enable_thinking", "thinking", "reasoning_effort", "thinking_budget",
        "temperature", "top_p", "stream", "stream_options")
    result = {"wire_generation_fields": {k: body[k] for k in safe_fields if k in body},
        "generation_body_without_messages_sha256": digest({k: v for k, v in body.items() if k != "messages"}),
        "placeholder_only": True, "provider_requests": 0}
    try:
        result.update(status="VERIFIED", contract=asdict(resolve_output_contract(config, body)))
    except ProviderError as error:
        result.update(status="BLOCKED", error_code=error.code)
    return result


def public_config(config):
    names = ("provider", "model", "model_id", "context_window", "max_output_tokens",
        "reasoning", "api", "embedding_api", "rerank_api", "source", "model_type", "dimensions")
    chat_names = ("max_tokens_field", "thinking_format", "thinking_always_send",
        "thinking_disable_on_non_stream", "supports_reasoning_effort", "thinking_budget_field")
    return {**{name: getattr(config, name) for name in names},
        "chat": {name: getattr(config.chat, name) for name in chat_names},
        "thinking_levels": dict(config.thinking_levels),
        "reranking_limits": {name: getattr(config.reranking, name) for name in
            ("max_documents", "max_query_chars", "max_document_chars", "max_request_chars")},
        "embedding_limits": {name: getattr(config.embedding, name) for name in
            ("max_batch_size", "max_input_chars")}}


def alignment_snapshot(runtime):
    """Metadata of the formal normal defaults; no request or configuration writes."""
    from backend.weknora_port.conversation_configuration import resolve_language
    from backend.weknora_port.retrieval.pipeline import (DEFAULT_REWRITE_SYSTEM,
        DEFAULT_REWRITE_USER, DEFAULT_CONTEXT_TEMPLATE)
    from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT
    conversation = asdict(runtime.conversation)
    summary = dict(runtime.conversation.summary)
    templates = {
        "rewrite_system": DEFAULT_REWRITE_SYSTEM if conversation["rewrite_prompt_system"] is None else conversation["rewrite_prompt_system"],
        "rewrite_user": DEFAULT_REWRITE_USER if conversation["rewrite_prompt_user"] is None else conversation["rewrite_prompt_user"],
        "context": summary.get("context_template", DEFAULT_CONTEXT_TEMPLATE),
        "generation_system": summary.get("prompt", DEFAULT_SYSTEM_PROMPT),
        "fallback": runtime._fallback_prompt(),
    }
    return {"conversation_settings": conversation,
        "router_policy": json.loads(json.dumps(asdict(runtime.policy), default=str)),
        "language": {"startup_locale": conversation["language_locale"],
            "request_accept_language": "zh-CN", "effective": resolve_language(conversation["language_locale"], "zh-CN")},
        "normal_request_reasoning_effort": "off",
        "prompt_template_sha256": {name: hashlib.sha256(value.encode()).hexdigest()
            for name, value in templates.items()},
        "intent_system_prompts_sha256": digest(conversation.get("intent_system_prompts", {}))}


def validate_alignment(actual, local):
    """Compare role identities/parameters across tenants, never tenant model IDs."""
    errors = []
    for role in ("cheap", "expensive", "embedding", "rerank", "vlm"):
        target = local.get("roles", {}).get(role)
        observed = actual.get("models", {}).get(role)
        if target is None or observed is None:
            errors.append("role_missing:" + role); continue
        for field in ("provider", "model", "source", "api", "parameters_sha256"):
            if observed.get(field) != target.get(field): errors.append(role + ":" + field)
    target_conversation = dict(local["conversation_settings"])
    observed_conversation = dict(actual["conversation_settings"])
    # These IDs are different owner identities for the same effective QU model.
    target_conversation.pop("query_understand_model_id", None)
    observed_conversation.pop("query_understand_model_id", None)
    # Older local runtime has no experiment field, whose normal default is false.
    # A/B/C isolation is checked independently by acceptance_settings and
    # validate_identity. Compare the shared normal baseline across tenants.
    target_conversation.pop("rerank_top1_floorless", None)
    observed_conversation.pop("rerank_top1_floorless", None)
    if observed_conversation != target_conversation: errors.append("conversation_settings")
    if actual["router_policy"] != local["router_policy"]: errors.append("router_policy")
    # DTO arrays serialize tuples and lists identically. Compare all fields in
    # their JSON representation, without dropping values or coercing scalars.
    dto_json = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if dto_json(actual["effective_generation_options"]) != dto_json(local["effective_generation_options_for_normal_request_off"]):
        errors.append("effective_generation_options")
    if any(value.get("status") != "VERIFIED" for value in actual["generation_output_contracts"].values()):
        errors.append("generation_output_contract")
    for key in ("prompt_template_sha256", "intent_system_prompts_sha256"):
        if key not in local: errors.append("local_snapshot_missing:" + key)
        elif actual.get(key) != local[key]: errors.append(key)
    qu_id = actual["conversation_settings"]["query_understand_model_id"]
    qu = actual.get("query_understanding", {}).get(qu_id, {}).get("config", {})
    target = local["query_understanding"]
    for field in ("provider", "model", "source"):
        if qu.get(field) != target.get(field): errors.append("QU:" + field)
    if not qu_id or qu_id != actual["models"]["cheap"]["model_id"]:
        errors.append("QU_explicit_current_cheap_identity")
    if errors: raise ValueError("S8_1_LOCAL_ALIGNMENT_MISMATCH:" + ",".join(errors))
    return {"status": "PASS", "tenant_model_ids_compared": False,
        "local_snapshot_sha256": digest(local), "experiment_switches": "validated separately against approved arm", "provider_requests": 0}


async def inspect_runtime(runtime, settings, local_alignment=None, *, require_closed=True):
    from scripts.s8_ecs_runtime import verify_runtime
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore
    from backend.weknora_port.conversation_configuration import resolved_chat_options
    from backend.weknora_port.models.types import ChatOptions
    if require_closed and (runtime.cloud_allowed is not False or getattr(runtime, "execution", None) is not None):
        raise ValueError("S8_1_INSPECTION_REQUIRES_CLOSED_CLOUD_NO_EXECUTION")
    identity = await verify_runtime(runtime, settings)
    async with runtime.leases.lease() as guard:
        revision, roles = await PostgresModelRoleStore(guard, runtime.principal,
            runtime.catalogue, cipher=runtime.cipher).read()
        kbs = await (await guard.connection.execute(
            "SELECT id,summary_model_id,embedding_model_id,cloud_allowed FROM knowledge_bases "
            "WHERE tenant_id=%s AND deleted_at IS NULL ORDER BY id", (332,))).fetchall()
    configs, details = {}, {}
    for role in roles:
        if role.role not in ("cheap", "expensive", "embedding", "rerank", "vlm"):
            continue
        if not role.provider: continue
        model_id = runtime.models._identity(role)[0]
        archived = await runtime.models.resolve(model_id)
        config = runtime.catalogue.resolve(archived, model_id)
        configs[role.role] = config
        details[role.role] = {**public_config(config), "parameters_sha256": hashlib.sha256(
            json.dumps(dict(role.parameters), sort_keys=True, default=str).encode()).hexdigest()}
    summary = dict(runtime.conversation.summary)
    # New normal sessions persist this exact summary. The existing S8 normal
    # request has reasoning_effort=off; no generation parameter is supplied here.
    options = resolved_chat_options(summary, "off")
    probes = {role: contract_probe(configs[role], options, stream=True)
              for role in ("cheap", "expensive")}
    from backend.weknora_port.model_selection import resolve_chat_model_id
    query_id = runtime.conversation.query_understand_model_id
    async def get_kb(identifier):
        return next((kb for kb in kbs if kb["id"] == identifier), None)
    async def get_model(identifier):
        row, _, _ = await runtime.models._load(identifier)
        return {name: row[name] for name in ("id", "type", "source")}
    async def list_models():
        rows = await runtime._rows("SELECT id FROM models WHERE tenant_id=%s AND subject_id=%s "
            "AND status='active' AND deleted_at IS NULL", (332, runtime.principal.subject_id))
        return [await get_model(row["id"]) for row in rows]
    qu_configs = {}
    for kb in kbs:
        chat_id = await resolve_chat_model_id("", "", (kb["id"],), get_kb, get_model, list_models)
        selected = query_id or chat_id
        kb["normal_chat_model_id"] = chat_id
        kb["normal_query_understand_model_id"] = selected
        archived = await runtime.models.resolve(selected)
        config = runtime.catalogue.resolve(archived, selected)
        qu_configs[selected] = {"config": public_config(config),
            "output_contract": contract_probe(config,
                ChatOptions(temperature=.3, max_completion_tokens=150, thinking=False), stream=False)}
    defaults = {name: getattr(runtime.conversation, name) for name in
        ("embedding_top_k", "rerank_top_k", "rerank_threshold", "vector_threshold",
         "keyword_threshold", "enable_rewrite", "enable_query_expansion", "enable_rerank",
         "source_evidence_mode", "rerank_context_enabled", "rerank_top1_floorless",
         "query_understand_model_id", "fallback_strategy")}
    # No assumption that one business rerank equals one Provider HTTP request.
    top = defaults["embedding_top_k"]
    candidate_upper = top + 5 * max(top * 2, defaults["rerank_top_k"] * 2)
    report = {**alignment_snapshot(runtime), "schema": "s8-1-runtime-inspection/v1", "mode": "ZERO_PROVIDER_CLOUD_FALSE" if require_closed else "AUTHORIZED_INSTANCE_ZERO_SEND_STARTUP_METADATA",
        "identity": identity, "registry_revision": revision, "models": details,
        "knowledge_base_bindings": kbs, "conversation_defaults": defaults,
        "summary_parameters": {k: v for k, v in summary.items() if k in ChatOptions.__dataclass_fields__},
        "summary_parameters_sha256": digest(summary), "effective_generation_options": asdict(options),
        "generation_output_contracts": probes, "query_understanding": qu_configs,
        "bound_analysis": {"single_kb_candidate_upper": candidate_upper,
            "fixed_non_rerank_calls_upper": 10,
            "provider_calls_upper": "10 + actual conservative rerank batch upper",
            "one_batch_conditional_upper": 11,
            "rerank_batch_count": "UNPROVEN; requires scoped passage size and Registry limits",
            "case_tokens_upper": "UNPROVEN; final dynamic wire inputs not yet bounded",
            "gate_reservation": "actual canonical wire_bytes + verified output_upper + 1024",
            "per_child_existing_limit": {"calls": 12, "tokens": 100000},
            "owner_parent_limit": {"question_posts": 13, "provider_calls": 143, "tokens": 400000}},
        "new_provider_requests": 0, "authorization_created": False,
        "database_role_write": False, "request_parameter_source":
            "runtime.create_session -> conversation.summary -> summary_parameters -> resolved_chat_options(summary, request.reasoning_effort)",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if (details["cheap"]["provider"], details["cheap"]["model"]) != ("siliconflow", "Qwen/Qwen3.5-4B"):
        raise ValueError("S8_1_OWNER_QWEN_BASELINE_REQUIRED")
    if not query_id or query_id != details["cheap"]["model_id"]:
        raise ValueError("S8_1_EXPLICIT_QWEN_QU_ENV_REQUIRED")
    if local_alignment is not None:
        report["local_alignment"] = validate_alignment(report, local_alignment)
    return report


async def inspect(output, environment=None, local_alignment=None):
    from scripts.s8_ecs_runtime import settings_and_factory
    env = dict(os.environ if environment is None else environment)
    if env.get("WEKNORA_PORT_CLOUD_ALLOWED", "false").lower() != "false" or env.get("WEKNORA_PORT_EXECUTION_FILE"):
        raise ValueError("S8_1_INSPECTION_REQUIRES_CLOSED_CLOUD_NO_EXECUTION")
    settings, factory, _ = settings_and_factory(env)
    async with factory(settings) as runtime:
        result = await inspect_runtime(runtime, settings, local_alignment)
    write_new(output, result)
    return result


# These hooks are installed only for the explicitly launched acceptance process.
# They call each original once, retain the returned object, and restore on exit.
def retrieval_observer(directory):
    from contextlib import contextmanager
    from contextvars import ContextVar
    from dataclasses import asdict, is_dataclass
    import uuid
    from backend.weknora_port.storage import hybrid
    from backend.weknora_port.storage.postgres import PostgresRetriever
    current = ContextVar("s8_1_retrieval_capture", default=None)
    def plain(value):
        return asdict(value) if is_dataclass(value) else value
    @contextmanager
    def install():
        originals = (hybrid.PostgresKnowledgeRepository.hybrid_search,
                     PostgresRetriever.vectors, PostgresRetriever.keywords,
                     hybrid.fuse_or_deduplicate)
        async def recall(self, targets, params, context, query_embedding=None):
            record = {"request_id": context.request_id, "scope": asdict(context.scope),
                      "targets": [asdict(t) for t in targets], "params": asdict(params),
                      "channels": [], "fusions": []}
            token = current.set(record)
            try:
                result = await originals[0](self, targets, params, context, query_embedding)
                record["returned_results"] = [plain(row) for row in result]
                return result
            finally:
                try: write_new(Path(directory) / (uuid.uuid4().hex + ".retrieval.json"), record)
                finally: current.reset(token)
        def channel(original, name):
            async def observed(self, params):
                result = await original(self, params)
                record = current.get()
                if record is not None:
                    safe_params = asdict(params)
                    embedding = safe_params.pop("embedding", ())
                    safe_params["embedding_dimensions"] = len(embedding)
                    record["channels"].append({"channel": name, "params": safe_params,
                                               "rows": [plain(row) for row in result]})
                return result
            return observed
        def fuse(vectors, keywords, config):
            result = originals[3](vectors, keywords, config)
            record = current.get()
            if record is not None:
                record["fusions"].append({"vector_input": [plain(row) for row in vectors],
                    "keyword_input": [plain(row) for row in keywords], "config": plain(config),
                    "full_fused_order": [plain(row) for row in result]})
            return result
        hybrid.PostgresKnowledgeRepository.hybrid_search = recall
        PostgresRetriever.vectors = channel(originals[1], "vector")
        PostgresRetriever.keywords = channel(originals[2], "bm25")
        hybrid.fuse_or_deduplicate = fuse
        try: yield
        finally:
            (hybrid.PostgresKnowledgeRepository.hybrid_search, PostgresRetriever.vectors,
             PostgresRetriever.keywords, hybrid.fuse_or_deduplicate) = originals
    return install()


def acceptance_settings(environment, arm, execution_file=None):
    from dataclasses import replace
    from scripts.s8_ecs_runtime import runtime_environment, trusted_principal
    from scripts.s8_evaluation_controller import ARMS
    from backend.weknora_port.development import DevelopmentSettings
    if arm not in ARMS: raise ValueError("S8_1_D_FORBIDDEN")
    # Check the unchanged production baseline before injecting this instance.
    env = runtime_environment(environment)
    if env["WEKNORA_PORT_CLOUD_ALLOWED"].lower() != "false" or env.get("WEKNORA_PORT_EXECUTION_FILE"):
        raise ValueError("S8_1_BASELINE_MUST_REMAIN_CLOSED")
    settings = DevelopmentSettings.from_environment(env)
    settings = replace(settings, cloud_allowed=execution_file is not None,
        execution_file=Path(execution_file) if execution_file is not None else None,
        graph_enabled=ARMS[arm]["graph"], rerank_top1_floorless=ARMS[arm]["floorless"])
    principal = trusted_principal(env.get("S8_PRINCIPAL_FILE", "/run/secrets/principal.json"))
    return settings, principal


def acceptance_application(args, environment=None, *, assemble_factory=None):
    from contextlib import asynccontextmanager
    from urllib.parse import urlsplit
    from dataclasses import replace
    from scripts.s8_ecs_runtime import api_token, verify_runtime, PrivateApiGuard
    from scripts.s8_evaluation_controller import (ARMS, read, canonical, sha, validate_plan,
        validate_identity, validate_authorization, request_payload)
    from backend.weknora_port.development import assemble
    from backend.weknora_port.entrypoint import create_app
    env = dict(os.environ if environment is None else environment)
    plan = validate_plan(read(args.plan))
    expected = read(args.inspection)
    settings, principal = acceptance_settings(env, args.arm, args.execution)
    base_url = getattr(args, "base_url", None) or f"http://127.0.0.1:{args.port}"
    if urlsplit(base_url).hostname != "127.0.0.1" or urlsplit(base_url).scheme != "http":
        raise ValueError("S8_1_LOOPBACK_HTTP_REQUIRED")
    settings = replace(settings, origins=(*settings.origins, base_url))
    selected = [row for row in plan["cases"] if row["case_id"] == args.case_id
                and args.arm in row.get("eligible_arms", ["A"])]
    if len(selected) != 1: raise ValueError("S8_1_SINGLE_APPROVED_CASE_REQUIRED")
    case = selected[0]
    if case.get("document_scope"): raise ValueError("S8_1_FULL_KB_SCOPE_REQUIRED")
    box = {}
    def authorize(identity):
        parent = read(args.authorization)
        if (parent["max_calls"] > 143 or parent["max_tokens"] > 400000
                or parent["end_to_end_total"] > 13):
            raise ValueError("S8_1_OWNER_LIMIT_EXCEEDED")
        ledger = validate_authorization(parent, args.execution, plan, identity, case, args.arm)
        if (ledger["max_calls"] != 11 or ledger["max_tokens"] > 100000
                or ledger.get("capture_evidence") is not True):
            raise ValueError("S8_1_CHILD_ENVELOPE_OR_CAPTURE_INVALID")
        if ledger["allowed_kbs"] != [case["knowledge_base_id"]]:
            raise ValueError("S8_1_EXACT_CHILD_KB_REQUIRED")
        if ledger["entries"]: raise ValueError("S8_1_FRESH_CHILD_REQUIRED_NO_RESEND")
        return ledger
    @asynccontextmanager
    async def factory(active):
        async with (assemble_factory or assemble)(active, principal=principal) as runtime:
            actual = await verify_runtime(runtime, active)
            if (active.query_understand_model_id != actual["models"]["cheap"]["model_id"]
                    or actual["models"]["cheap"]["model"] != "Qwen/Qwen3.5-4B"):
                raise ValueError("S8_1_EXPLICIT_QWEN_QU_ENV_REQUIRED")
            if getattr(args, "local_alignment", None):
                # Startup metadata read uses the real authorized runtime;
                # no model method/transport is invoked by inspect_runtime.
                await inspect_runtime(runtime, active, read(args.local_alignment), require_closed=False)
            frozen = expected["identity"]
            for key in ("tenant_id", "subject_sha256", "database", "postgres_system_id",
                        "redis_run_id", "storage_root", "models"):
                if actual[key] != frozen[key]: raise ValueError("S8_1_FROZEN_RUNTIME_CHANGED")
            if digest(dict(runtime.conversation.summary)) != expected["summary_parameters_sha256"]:
                raise ValueError("S8_1_GENERATION_DEFAULTS_CHANGED")
            resource = {"postgres_system_id": active.system_id, "database": active.database,
                "redis_run_id": active.redis_run_id, "redis_db": int(urlsplit(active.redis_url).path[1:])}
            identity = {"tenant_id": 332, "subject_sha256": actual["subject_sha256"],
                "storage_root": str(active.storage), "runtime_identity_sha256": sha(canonical(resource)),
                "base_url": base_url,
                "graph_enabled": ARMS[args.arm]["graph"],
                "rerank_top1_floorless": ARMS[args.arm]["floorless"],
                "model_binding_snapshot": plan["model_binding_snapshot"]}
            configured = plan["model_binding_snapshot"]["configured_roles"]
            if configured != actual["models"] or plan["model_binding_snapshot"]["effective_roles"] != configured:
                raise ValueError("S8_1_REGISTRY_OVERLAY_FORBIDDEN")
            validate_identity(plan, identity, args.arm)
            ledger = authorize(identity)
            parent_proof = digest(read(args.authorization))
            original_install = runtime.execution.install_gate
            def guarded_install(gate):
                original_install(gate)
                if getattr(gate, "_s8_1_parent_checked", False): return gate
                original_authorize = gate.authorize
                async def checked(call):
                    from backend.weknora_port.models.types import ProviderError
                    if digest(read(args.authorization)) != parent_proof:
                        raise ProviderError("S8_1_PARENT_AUTHORIZATION_CHANGED_STOP")
                    return await original_authorize(call)
                gate.authorize, gate._s8_1_parent_checked = checked, True
                return gate
            runtime.execution.install_gate = guarded_install
            write_new(Path(args.execution).with_suffix(".instance-claim.json"),
                {"case_id": args.case_id, "arm": args.arm, "pid": os.getpid(), "parent_sha256": parent_proof})
            box.update(identity=identity)
            startup = {**identity, "cloud_allowed": True, "worker_started": False,
                "database_roles_changed": False, "request_generation_defaults_changed": False,
                "child_hard_envelope": {"calls": 11, "tokens": ledger["max_tokens"]},
                "dynamic_wire_sufficiency": "NOT_GUARANTEED_FAIL_CLOSED_AT_EXISTING_GATE"}
            write_new(args.output, startup)
            with retrieval_observer(Path(args.execution).parent / "evidence"):
                yield runtime
    class SingleQuestion:
        def __init__(self, app): self.app, self.claimed = app, False
        async def __call__(self, scope, receive, send):
            if scope["type"] != "http": return await self.app(scope, receive, send)
            method, path = scope["method"], scope["path"]
            if method in ("PUT", "PATCH", "DELETE"):
                return await PrivateApiGuard.response(send, 403, {"error": {"code": "S8_1_READONLY_EXCEPT_QA"}})
            if method == "POST" and path.endswith("/messages"):
                chunks = []
                while True:
                    frame = await receive(); chunks.append(frame)
                    if frame["type"] != "http.request" or not frame.get("more_body"): break
                try:
                    if self.claimed: raise ValueError("S8_1_QUESTION_ALREADY_CLAIMED")
                    payload = json.loads(b"".join(frame.get("body", b"") for frame in chunks))
                    if payload != request_payload(case, payload.get("request_id")):
                        raise ValueError("S8_1_FROZEN_QUESTION_REQUIRED")
                    authorize(box["identity"])
                    write_new(Path(args.output).with_suffix(".post-claim.json"),
                              {"case_id": args.case_id, "arm": args.arm, "request_id": payload.get("request_id")})
                    self.claimed = True
                except (ValueError, KeyError, FileExistsError):
                    return await PrivateApiGuard.response(send, 409, {"error": {"code": "S8_1_QUESTION_ADMISSION_DENIED"}})
                queued = iter(chunks)
                async def replay():
                    try: return next(queued)
                    except StopIteration: return await receive()
                return await self.app(scope, replay, send)
            if method == "POST" and path != "/api/v1/sessions":
                return await PrivateApiGuard.response(send, 403, {"error": {"code": "S8_1_POST_NOT_APPROVED"}})
            await self.app(scope, receive, send)
    async def readiness():
        return (200, box["identity"]) if "identity" in box else (503, {"error": {"code": "S8_1_STARTING"}})
    return PrivateApiGuard(SingleQuestion(create_app(settings=settings, runtime_factory=factory)),
                           api_token(env), readiness)


def serve(args):
    import uvicorn
    app = acceptance_application(args)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning", access_log=False)

from scripts.s8_evaluation_controller import S8Controller


class AcceptanceController(S8Controller):
    def validate_case_limits(self, parent, case):
        from scripts.s8_evaluation_controller import case_key
        if Path(parent.get("checkpoint_path", "")).resolve() != self.path.resolve():
            raise ValueError("S8_PARENT_FIXED_CHECKPOINT_REQUIRED")
        if (parent.get("max_case_arms") != {"A": 9, "B": 2, "C": 2}
                or parent.get("end_to_end_total") != 13):
            raise ValueError("S8_1_OWNER_CASE_LIMITS_REQUIRED")
        entries = list(self.checkpoint["entries"].values())
        if len(entries) >= 13 or sum(row.get("arm") == self.arm for row in entries) >= parent["max_case_arms"][self.arm]:
            raise ValueError("S8_CASE_OR_ARM_LIMIT_DENIED")
        if self.arm != "A" and not any(row.get("arm") == "A" and row.get("status") == "COMPLETE" for row in entries):
            raise ValueError("S8_1_COMPLETED_A_CHAIN_REQUIRED")
        if self.arm != "A" and case_key(case, "A") not in self.checkpoint["entries"]:
            raise ValueError("S8_1_SAME_A_QUESTION_REQUIRED")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "serve", "run", "resume"))
    parser.add_argument("--output", type=Path, required=True)
    for name in ("plan", "inspection", "authorization", "execution", "identity", "checkpoint"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--arm", choices=("A", "B", "C"))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--base-url")
    parser.add_argument("--local-alignment", type=Path)
    args = parser.parse_args(argv)
    if args.command in ("run", "resume"):
        from scripts.s8_evaluation_controller import S8ApiClient, read, case_key
        from scripts.s8_ecs_runtime import api_token
        if not all(getattr(args, name) for name in ("plan", "identity", "checkpoint", "case_id", "arm")):
            parser.error("run/resume requires plan/identity/checkpoint/case-id/arm")
        plan, identity = read(args.plan), read(args.identity)
        selected = [case for case in plan["cases"] if case["case_id"] == args.case_id]
        if len(selected) != 1: raise ValueError("S8_1_SINGLE_APPROVED_CASE_REQUIRED")
        client = S8ApiClient(identity["base_url"], api_token())
        try:
            controller = AcceptanceController(plan, args.arm, identity, client, args.checkpoint)
            controller.preflight_api()
            if args.command == "run":
                if not args.authorization or not args.execution:
                    parser.error("run requires authorization/execution")
                result = controller.run_case(selected[0], args.authorization, args.execution, args.output)
            else:
                result = controller.resume_readonly(selected[0], args.output / case_key(selected[0], args.arm))
            print(json.dumps(result, ensure_ascii=False, default=str))
        finally: client.close()
        return 0
    if args.command == "serve":
        if not all(getattr(args, name) for name in ("plan", "inspection", "authorization", "execution", "case_id", "arm")):
            parser.error("serve requires plan/inspection/authorization/execution/case-id/arm")
        serve(args)
        return 0
    local = json.loads(args.local_alignment.read_text(encoding="utf-8-sig")) if args.local_alignment else None
    asyncio.run(inspect(args.output, local_alignment=local))
    print(json.dumps({"status": "ZERO_SEND_INSPECTION_SAVED", "output": str(args.output),
                      "provider_requests": 0, "authorization_created": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
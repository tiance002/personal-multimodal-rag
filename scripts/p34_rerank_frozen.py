"""P34 one-shot replay of frozen P32 SiliconFlow rerank requests.

This diagnostic only calls the currently configured Rerank role. It never
rewrites queries, searches, embeds, answers, or changes persisted settings.
The request cap is four, and an existing send manifest permanently prevents
the script from resending the same P34 batch.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import traceback
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RUN_ROOT = Path(r"E:\WK\run\p34-rerank-20261017")
P32_REQUESTS = Path(r"E:\WK\run\p32-20261017\evidence-332\provider-requests")
P33_TRACE = ROOT / "docs/evaluation/evidence/p33-evidence-loss-20261017/frozen-run-trace-summary.json"
MODEL_ID = "mdl3076be3c32a0a17f8e9cf5deef89452a9f8886163b0aebd8aa4d1130958e3"
EXPECTED_MODEL = "BAAI/bge-reranker-v2-m3"
EXPECTED_PROVIDER = "siliconflow"

CASES = (
    ("mml-0434", 14, "085643dc-5ebb-4b8a-b7f1-75427da40015"),
    ("hotpot-5a7631", 14, "31417c7c-7abf-4723-956a-74bf7ee14683"),
    ("omni-page007", 4, "51316a20-67fd-4732-b520-c049999933d0"),
    ("mml-0959", 1, "df6098cb-68e6-4546-b9e0-8682b4bf19e9"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_wire(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True, allow_nan=False).encode("utf-8")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def atomic_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def load_frozen() -> list[dict]:
    trace = json.loads(P33_TRACE.read_text(encoding="utf-8"))
    trace_cases = {item["case_id"]: item for item in trace["cases"]}
    output = []
    for case_id, target_index, target_id in CASES:
        trace_case = trace_cases.get(case_id)
        if trace_case is None:
            raise RuntimeError(f"{case_id}:P33_CASE_MISSING")
        capture_path = P32_REQUESTS / Path(trace_case["artifact_paths"]["rerank_request"]).name
        capture = json.loads(capture_path.read_text(encoding="utf-8"))
        body = capture.get("body")
        if not isinstance(body, dict) or set(body) != {"model", "query", "documents"}:
            raise RuntimeError(f"{case_id}:FROZEN_BODY_SHAPE_MISMATCH")
        wire = canonical_wire(body)
        candidates = trace_case.get("search", {}).get("ranked_candidates")
        if not isinstance(candidates, list) or len(candidates) != 30:
            raise RuntimeError(f"{case_id}:P33_CANDIDATE_MAPPING_INCOMPLETE")
        documents = body["documents"]
        if len(documents) != 30 or len(candidates) != len(documents):
            raise RuntimeError(f"{case_id}:CANDIDATE_COUNT_MISMATCH")
        if capture.get("provider") != EXPECTED_PROVIDER or capture.get("model") != EXPECTED_MODEL:
            raise RuntimeError(f"{case_id}:FROZEN_MODEL_IDENTITY_MISMATCH")
        if body.get("model") != EXPECTED_MODEL:
            raise RuntimeError(f"{case_id}:FROZEN_BODY_MODEL_MISMATCH")
        if sha256(wire) != capture.get("wire_sha256") or len(wire) != capture.get("wire_bytes"):
            raise RuntimeError(f"{case_id}:FROZEN_WIRE_REBUILD_MISMATCH")
        if target_index >= len(candidates) or candidates[target_index].get("id") != target_id:
            raise RuntimeError(f"{case_id}:TARGET_INDEX_MAPPING_MISMATCH")
        mapping = []
        for index, (text, candidate) in enumerate(zip(documents, candidates, strict=True)):
            digest = sha256(text.encode("utf-8"))
            mapping.append({
                "provider_input_index": index,
                "chunk_id": candidate["id"],
                "knowledge_base_id": candidate["knowledge_base_id"],
                "document_id": candidate["knowledge_id"],
                "chunk_index": candidate["chunk_index"],
                "chunk_type": candidate["chunk_type"],
                "provider_input_text_sha256": digest,
                "source_chunk_content_sha256": candidate["content_sha256"],
                "content_chars": len(text),
                "search_rank": candidate["rank"],
            })
        if capture.get("boundary") != "HTTPX_REQUEST_HOOK_AFTER_DURABLE_GATE":
            raise RuntimeError(f"{case_id}:FROZEN_CAPTURE_BOUNDARY_MISMATCH")
        if (trace_case.get("rerank", {}).get("request_wire_sha256") != capture.get("wire_sha256")
                or trace_case.get("rerank", {}).get("request_id") != capture.get("request_id")):
            raise RuntimeError(f"{case_id}:P33_CAPTURE_IDENTITY_MISMATCH")
        output.append({
            "case_id": case_id,
            "historical_request_id": capture["request_id"],
            "capture_path": str(capture_path),
            "capture_boundary": capture["boundary"],
            "provider": capture["provider"],
            "model": capture["model"],
            "query": body["query"],
            "input_count": len(documents),
            "target_input_index": target_index,
            "target_chunk_id": target_id,
            "target_candidate": mapping[target_index],
            "target_source_chunk_content_sha256": candidates[target_index]["content_sha256"],
            "knowledge_base_id": mapping[target_index]["knowledge_base_id"],
            "request_sha256": sha256(wire),
            "request_bytes": len(wire),
            "request_body_keys": sorted(body),
            "candidate_map": mapping,
            "_body": body,
            "_trace": trace_case,
        })
    return output


def safe_usage(usage) -> dict:
    return {name: getattr(usage, name, None) for name in (
        "prompt_tokens", "completion_tokens", "total_tokens", "status",
        "cached_tokens", "cache_read_tokens", "cache_write_tokens", "cache_miss_tokens",
        "cache_reported", "cache_status",
    )}


def public_case(case: dict) -> dict:
    return {k: v for k, v in case.items() if not k.startswith("_")}


async def run(send: bool) -> int:
    if len(CASES) > 4:
        raise RuntimeError("P34_CALL_CAP_EXCEEDED")
    frozen = load_frozen()

    # Reuse P32's isolated service environment recipe without starting HTTP/API
    # services. The only change is a P34-specific Redis key prefix; this direct
    # adapter test performs no Redis writes. Cloud permission is a process-local
    # setting for this Owner-authorized four-call scope, not a stored toggle.
    from scripts.p32_real_acceptance import child_env, state
    state_data = state()
    primary = state_data["primary_tenant"]
    env = child_env(332, primary["subject_id"], 56425, primary["storage_root"],
                    primary["redis_prefix"], primary["api_evidence"])
    expected_prefix = "weknora-python-p34-rerank-20261017:332:" + hashlib.sha256(
        "|".join(item["request_sha256"] for item in frozen).encode()).hexdigest()[:20]
    env["WEKNORA_PORT_REDIS_PREFIX"] = expected_prefix
    allowed_env = {name for name in env if name.startswith("WEKNORA_PORT_") or name in {
        "PYTHONPATH", "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE", "TEMP", "TMP", "TMPDIR",
        "XDG_CACHE_HOME", "RAG_TESSDATA_DIR",
    }}
    for name in allowed_env:
        os.environ[name] = env[name]

    marker = Path(primary["storage_root"]) / ".weknora-python-storage.json"
    if not marker.is_file():
        raise RuntimeError("P34_STORAGE_IDENTITY_MARKER_MISSING")
    marker_value = json.loads(marker.read_text(encoding="utf-8"))
    if marker_value != {"database": "weknora_python_port_20261013", "system_id": "7696088403504697382"}:
        raise RuntimeError("P34_STORAGE_IDENTITY_MISMATCH")

    from backend.weknora_port.development import DevelopmentSettings, assemble
    from backend.weknora_port.models.request import build_rerank_body, split_batches
    from backend.weknora_port.models.types import CallContext, RerankResponse, TrustedScope
    from backend.weknora_port.retrieval.merge import collect_image_info
    from backend.weknora_port.retrieval.rerank import enriched_passage
    from backend.weknora_port.retrieval.search import SearchTarget
    from backend.weknora_port.runtime import LeasedKnowledgeRepository, LeasedSendGate
    from backend.weknora_port.storage.configuration import ConfiguredRole
    from backend.weknora_port.storage.history import LocalPrincipal

    settings = DevelopmentSettings.from_environment()
    if not settings.cloud_allowed:
        raise RuntimeError("P34_GLOBAL_CLOUD_GATE_DISABLED")
    if settings.redis_prefix != expected_prefix:
        raise RuntimeError("P34_REDIS_NAMESPACE_MISMATCH")

    if send and (RUN_ROOT / "send-manifest.json").exists():
        raise RuntimeError("P34_SEND_MANIFEST_EXISTS_NO_REPLAY")
    existing = list(RUN_ROOT.iterdir()) if RUN_ROOT.exists() else []
    if send and any(path.name != "preflight.json" for path in existing):
        raise RuntimeError("P34_RUN_DIR_NOT_EMPTY_NO_REPLAY")
    RUN_ROOT.mkdir(parents=True, exist_ok=True)

    preflight = {
        "schema": "p34-rerank-preflight/v1",
        "created_at": utc_now(),
        "source_sha": "f8ec9c548e48ef565f5011b8a02a705d4bfabb7c",
        "fixed_upstream_sha": "3e8b0bfc80b845b2d4b2ed683994748741450a97",
        "owner_authorization": "current P34 user instruction: at most four SiliconFlow rerank calls",
        "account_token_fence": "not restored; P31 revoked",
        "global_cloud_process_setting": "true, temporary P32 isolated environment recipe under P34 request scope",
        "persistent_kb_consent_changed": False,
        "other_provider_calls": 0,
        "cases": [],
    }

    async with assemble(settings, principal=LocalPrincipal(332, primary["subject_id"])) as app:
        if app.principal.tenant_id != 332 or app.principal.subject_id != primary["subject_id"]:
            raise RuntimeError("P34_PRINCIPAL_IDENTITY_MISMATCH")
        if app.cloud_allowed is not True:
            raise RuntimeError("P34_GLOBAL_CLOUD_GATE_DISABLED")
        registries = []
        # First reproduce all 120 provider candidate strings from the persisted
        # source chunks and image-child enrichment, without touching secrets.
        # This keeps input fidelity independently auditable if credentials are
        # unavailable and guarantees no call can happen before every case passes.
        for case in frozen:
            rows = await app._rows(
                "SELECT id,cloud_allowed FROM knowledge_bases WHERE tenant_id=%s AND id=%s AND deleted_at IS NULL",
                (app.principal.tenant_id, case["knowledge_base_id"]))
            if len(rows) != 1 or rows[0]["cloud_allowed"] is not True:
                raise RuntimeError(f"{case['case_id']}:P34_KB_CLOUD_CONSENT_NOT_ENABLED")
            context = CallContext(
                request_id="p34-preflight-" + case["case_id"],
                scope=TrustedScope(app.principal.subject_id, (case["knowledge_base_id"],), ()))
            registry = app.registry_for_context()
            try:
                repository = LeasedKnowledgeRepository(app, registry)
                ids = [candidate["chunk_id"] for candidate in case["candidate_map"]]
                source_rows = await repository.get_chunks(ids, context)
                if set(source_rows) != set(ids):
                    raise RuntimeError(f"{case['case_id']}:INPUT_NOT_REPRODUCIBLE_SOURCE_ROWS_MISSING")
                for index, candidate in enumerate(case["candidate_map"]):
                    row = source_rows[candidate["chunk_id"]]
                    if (row.knowledge_base_id != case["knowledge_base_id"]
                            or sha256(row.content.encode("utf-8")) != candidate["source_chunk_content_sha256"]):
                        raise RuntimeError(f"{case['case_id']}:INPUT_NOT_REPRODUCIBLE_SOURCE_IDENTITY:{index}")
                state_for_images = SimpleNamespace(targets=(SearchTarget(case["knowledge_base_id"]),))
                empty_image_ids = [candidate["chunk_id"] for candidate in case["candidate_map"]
                                   if not source_rows[candidate["chunk_id"]].image_info]
                image_infos = await collect_image_info(
                    empty_image_ids, state_for_images, context, repository, source_chunks=source_rows)
                mismatches = []
                for index, candidate in enumerate(case["candidate_map"]):
                    row = source_rows[candidate["chunk_id"]]
                    if not row.image_info and candidate["chunk_id"] in image_infos:
                        row.image_info = image_infos[candidate["chunk_id"]]
                    actual_text = enriched_passage(row)
                    if sha256(actual_text.encode("utf-8")) != candidate["provider_input_text_sha256"]:
                        mismatches.append(index)
                if mismatches:
                    raise RuntimeError(f"{case['case_id']}:INPUT_NOT_REPRODUCIBLE_ENRICHED_TEXT:" + ",".join(map(str, mismatches)))
                case["_registry"] = registry
                case["_context"] = context
                registries.append(registry)
                preflight["cases"].append({
                    **public_case(case),
                    "score_threshold": 0.3,
                    "top1_fallback_floor": 0.15,
                    "kb_cloud_consent": True,
                    "source_rows_and_enriched_input_reproduced": True,
                    "source_row_count": len(source_rows),
                })
            except BaseException:
                await registry.close()
                raise

        preflight["input_reproduction"] = "PASS"
        preflight["credential_decryption"] = "NOT_RUN"
        preflight["provider_calls"] = 0
        role_rows = await app._rows(
            "SELECT revision,roles->'rerank' AS role FROM port_model_configuration "
            "WHERE tenant_id=%s AND subject_id=%s",
            (app.principal.tenant_id, app.principal.subject_id))
        if len(role_rows) != 1 or not isinstance(role_rows[0]["role"], dict):
            preflight["status"] = "BLOCKED_RERANK_ROLE_NOT_CONFIGURED"
            atomic_json(RUN_ROOT / "preflight.json", preflight)
            for registry in registries:
                await registry.close()
            print("P34_BLOCKED " + json.dumps({"status": preflight["status"], "provider_calls": 0,
                                               "evidence": str(RUN_ROOT / "preflight.json")}, ensure_ascii=False))
            return 2
        stored_role = dict(role_rows[0]["role"])
        if (stored_role.get("role") != "rerank" or stored_role.get("provider") != EXPECTED_PROVIDER
                or stored_role.get("model") != EXPECTED_MODEL):
            raise RuntimeError("P34_RERANK_ROLE_IDENTITY_MISMATCH")
        stored_key = stored_role.get("api_key", "")
        if not isinstance(stored_key, str) or not stored_key:
            preflight["status"] = "BLOCKED_RERANK_CREDENTIAL_NOT_CONFIGURED"
            atomic_json(RUN_ROOT / "preflight.json", preflight)
            for registry in registries:
                await registry.close()
            print("P34_BLOCKED " + json.dumps({"status": preflight["status"], "provider_calls": 0,
                                               "evidence": str(RUN_ROOT / "preflight.json")}, ensure_ascii=False))
            return 2
        try:
            stored_role["api_key"] = app.cipher.decrypt(stored_key, strict=True)
        except ValueError:
            preflight["status"] = "BLOCKED_STORED_SECRET_DECRYPT_FAILED"
            preflight["credential_decryption"] = "FAILED"
            preflight["credential_error_code"] = "STORED_SECRET_DECRYPT_FAILED"
            preflight["provider_calls"] = 0
            for item in preflight["cases"]:
                item["current_config_match"] = "NOT_RUN"
                item["native_filter_replay"] = "NOT_RUN_NO_MODEL_RESPONSE"
            atomic_json(RUN_ROOT / "preflight.json", preflight)
            for registry in registries:
                await registry.close()
            print("P34_BLOCKED " + json.dumps({"status": preflight["status"], "provider_calls": 0,
                                               "evidence": str(RUN_ROOT / "preflight.json")}, ensure_ascii=False))
            return 2

        role = ConfiguredRole(**stored_role)
        computed_model_id, _, physical_sha, _ = app.models._identity(role)
        active_rows = await app._rows(
            "SELECT status,physical_sha256 FROM models WHERE tenant_id=%s AND subject_id=%s "
            "AND id=%s AND deleted_at IS NULL",
            (app.principal.tenant_id, app.principal.subject_id, MODEL_ID))
        if (computed_model_id != MODEL_ID or len(active_rows) != 1
                or active_rows[0]["status"] != "active" or active_rows[0]["physical_sha256"] != physical_sha):
            raise RuntimeError("P34_ACTIVE_RERANK_MODEL_IDENTITY_MISMATCH")
        config = app.catalogue.resolve(role, MODEL_ID)
        if (role.role != "rerank" or role.provider != EXPECTED_PROVIDER or role.model != EXPECTED_MODEL
                or not role.api_key or config.model_id != MODEL_ID
                or config.provider != EXPECTED_PROVIDER or config.model != EXPECTED_MODEL):
            raise RuntimeError("P34_ACTIVE_RERANK_MODEL_IDENTITY_MISMATCH")
        if config.reranking.score_scale != "probability":
            raise RuntimeError("P34_RERANK_SCORE_SCALE_NOT_PROBABILITY")
        probe_model = await app._make_model(config, role.api_key, LeasedSendGate(app))
        try:
            for case, item in zip(frozen, preflight["cases"], strict=True):
                body = build_rerank_body(config, case["query"], case["_body"]["documents"])
                wire = canonical_wire(body)
                if sha256(wire) != case["request_sha256"] or len(wire) != case["request_bytes"]:
                    raise RuntimeError(f"{case['case_id']}:CURRENT_CONFIG_WIRE_MISMATCH")
                batches = split_batches(
                    tuple(case["_body"]["documents"]),
                    max_items=config.reranking.max_documents,
                    max_item_chars=config.reranking.max_document_chars,
                    max_total_chars=config.reranking.max_request_chars,
                    fixed_chars=len(case["query"]))
                if len(batches) != 1 or batches[0][0] != 0 or list(batches[0][1]) != case["_body"]["documents"]:
                    raise RuntimeError(f"{case['case_id']}:CURRENT_CONFIG_WOULD_SPLIT_FROZEN_INPUT")
                item.update({
                    "current_config_wire_sha256": sha256(wire),
                    "current_config_wire_bytes": len(wire),
                    "current_config_matches_frozen": True,
                    "current_config_extra_params": sorted(set(body) - {"model", "query", "documents"}),
                    "score_scale": config.reranking.score_scale,
                    "model_active_and_owner_scoped": True,
                    "credential_configured_without_disclosure": True,
                })
                case["_config"] = config
                case["_role"] = role
        finally:
            await probe_model.transport.aclose()
        preflight["credential_decryption"] = "PASS"
        preflight["status"] = "PREFLIGHT_PASS"

        atomic_json(RUN_ROOT / "preflight.json", preflight)
        if not send:
            for case in frozen:
                await case["_registry"].close()
            print("P34_PREFLIGHT_PASS " + json.dumps({
                "cases": len(frozen), "wire_matches": True, "candidate_maps_match": True,
                "provider_calls": 0, "cloud_process_setting": settings.cloud_allowed,
                "kb_consents": "all true", "result": str(RUN_ROOT / "preflight.json")}, ensure_ascii=False))
            return 0

        plan_cases = []
        for case in frozen:
            plan_cases.append({
                "case_id": case["case_id"],
                "request_id": "p34-" + case["case_id"] + "-" + uuid.uuid4().hex,
                "status": "PLANNED",
                "request_sha256": case["request_sha256"],
                "target_chunk_id": case["target_chunk_id"],
                "target_input_index": case["target_input_index"],
            })
        manifest = {
            "schema": "p34-rerank-send-manifest/v1",
            "created_at": utc_now(),
            "status": "PREPARED",
            "owner_authorization": preflight["owner_authorization"],
            "max_real_rerank_requests": 4,
            "planned_real_rerank_requests": len(plan_cases),
            "automatic_retries": False,
            "token_authority_restored": False,
            "cases": plan_cases,
        }
        atomic_json(RUN_ROOT / "send-manifest.json", manifest)

        from backend.weknora_port.retrieval.rerank import rerank as native_rerank
        from backend.weknora_port.models.types import RerankResult

        real_calls = 0
        for ordinal, (case, planned) in enumerate(zip(frozen, plan_cases, strict=True)):
            model = await app._make_model(case["_config"], case["_role"].api_key, LeasedSendGate(app))
            registry = case["_registry"]
            transport = model.transport
            original_send = transport.client.send
            capture = {"entered_send": False, "response_path": str(RUN_ROOT / (case["case_id"] + ".response.body"))}

            async def observed_send(request, *args, **kwargs):
                nonlocal real_calls
                capture["entered_send"] = True
                request_body = request.content
                capture["request_sha256"] = sha256(request_body)
                capture["request_bytes"] = len(request_body)
                capture["endpoint_host"] = request.url.host
                capture["sent_at"] = utc_now()
                started = time.perf_counter()
                real_calls += 1
                if real_calls > 4:
                    raise RuntimeError("P34_REAL_CALL_CAP_EXCEEDED")
                try:
                    response = await original_send(request, *args, **kwargs)
                    raw = await response.aread()
                    atomic_bytes(Path(capture["response_path"]), raw)
                    capture.update({
                        "http_status": response.status_code,
                        "response_sha256": sha256(raw),
                        "response_bytes": len(raw),
                        "response_received_at": utc_now(),
                        "http_elapsed_seconds": round(time.perf_counter() - started, 6),
                    })
                    return response
                except BaseException as error:
                    capture.update({
                        "transport_error_class": type(error).__name__,
                        "transport_error_at": utc_now(),
                        "http_elapsed_seconds": round(time.perf_counter() - started, 6),
                    })
                    raise

            transport.client.send = observed_send
            planned["status"] = "SENDING"
            planned["started_at"] = utc_now()
            manifest["status"] = "IN_PROGRESS"
            atomic_json(RUN_ROOT / "send-manifest.json", manifest)
            context = CallContext(
                request_id=planned["request_id"],
                scope=TrustedScope(app.principal.subject_id, (case["knowledge_base_id"],), ()))
            start = time.perf_counter()
            adapter_error = None
            response_obj = None
            try:
                response_obj = await model.rerank(case["query"], case["_body"]["documents"], context)
                planned["status"] = "RESPONSE_RECEIVED"
                planned["usage"] = safe_usage(response_obj.usage)
            except BaseException as error:
                adapter_error = type(error).__name__
                planned["status"] = ("RESPONSE_RECEIVED_ADAPTER_ERROR" if capture.get("http_status") is not None
                                      else "UNKNOWN" if capture["entered_send"] else "NOT_SENT")
            finally:
                transport.client.send = original_send
                await transport.aclose()
            elapsed = round(time.perf_counter() - start, 6)
            planned["completed_at"] = utc_now()
            planned["elapsed_seconds"] = elapsed
            planned["adapter_error_class"] = adapter_error
            planned["provider_capture"] = {k: v for k, v in capture.items() if k != "response_path"}
            planned["raw_response_path"] = capture["response_path"] if capture.get("response_sha256") else None
            if capture.get("request_sha256") != case["request_sha256"]:
                planned["wire_matches_frozen"] = False
                planned["status"] = "RESPONSE_RECEIVED_WIRE_MISMATCH" if capture.get("http_status") else "UNKNOWN"
            else:
                planned["wire_matches_frozen"] = True

            scores = []
            if response_obj is not None:
                indices = [row.index for row in response_obj.results]
                duplicate_indices = len(indices) != len(set(indices))
                invalid_indices = any(type(index) is not int or not 0 <= index < 30 for index in indices)
                for rank, row in enumerate(response_obj.results, start=1):
                    candidate = case["candidate_map"][row.index] if 0 <= row.index < 30 else None
                    scores.append({
                        "provider_return_order": rank,
                        "provider_input_index": row.index,
                        "chunk_id": candidate["chunk_id"] if candidate else None,
                        "chunk_type": candidate["chunk_type"] if candidate else None,
                        "relevance_score": row.score,
                        "is_target": bool(candidate and candidate["chunk_id"] == case["target_chunk_id"]),
                    })
                planned["response_result_count"] = len(scores)
                planned["duplicate_indices"] = duplicate_indices
                planned["invalid_indices"] = invalid_indices
                planned["score_scale"] = response_obj.score_scale
                planned["scores"] = scores
                by_score = sorted(scores, key=lambda item: (-item["relevance_score"], item["provider_return_order"]))
                planned["score_ranked_results"] = [
                    {**item, "score_rank": rank} for rank, item in enumerate(by_score, start=1)]
                target_scored = next((item for item in scores if item["is_target"]), None)
                planned["target_score"] = target_scored["relevance_score"] if target_scored else None
                planned["target_provider_return_order"] = target_scored["provider_return_order"] if target_scored else None
                planned["target_score_rank"] = next((item["score_rank"] for item in planned["score_ranked_results"]
                                                     if item["is_target"]), None)
                top = by_score[0] if by_score else None
                planned["highest_score"] = top["relevance_score"] if top else None
                planned["highest_score_chunk_id"] = top["chunk_id"] if top else None
                if not duplicate_indices and not invalid_indices and scores:
                    replay_candidates = []
                    for index, item in enumerate(case["candidate_map"]):
                        replay_candidates.append(SimpleNamespace(
                            id=item["chunk_id"], content=case["_body"]["documents"][index],
                            image_info="[]", chunk_metadata={}, metadata={},
                            score=case["_trace"]["search"]["ranked_candidates"][index]["score"],
                            knowledge_source="knowledge_base", chunk_type=item["chunk_type"]))
                    replay_state = SimpleNamespace(
                        needs_retrieval=True, search_results=replay_candidates,
                        rerank_model_id="p34-offline-response-replay", rewrite_query=case["query"],
                        rerank_threshold=0.3, targets=[SimpleNamespace(disable_recall_thresholds=False)],
                        rerank_results=[], rerank_top_k=30,
                        faq_priority_enabled=False, faq_score_boost=1.0)

                    class ReplayModel:
                        async def rerank(self, query, documents, replay_context):
                            return RerankResponse(response_obj.results, response_obj.usage, response_obj.score_scale)

                    class ReplayRegistry:
                        async def rerank(self, model_id, replay_context):
                            return ReplayModel()

                    replay_context = CallContext(
                        request_id="p34-offline-replay-" + case["case_id"],
                        scope=TrustedScope(app.principal.subject_id, (case["knowledge_base_id"],), ()))
                    try:
                        await native_rerank(replay_state, replay_context, ReplayRegistry())
                        kept = [result.id for result in replay_state.rerank_results]
                        planned["native_filter_replay"] = {
                            "threshold": 0.3,
                            "top1_fallback_floor": 0.15,
                            "filter_logic": "backend.weknora_port.retrieval.rerank.rerank",
                            "replay_rerank_top_k": 30,
                            "retained_chunk_ids_after_native_filter_and_mmr": kept,
                            "retained_count": len(kept),
                            "target_retained": case["target_chunk_id"] in kept,
                            "top1_fallback_applies": not any(row.score >= 0.3 for row in response_obj.results)
                                and bool(response_obj.results) and response_obj.results[0].score >= 0.15,
                        }
                    except Exception as error:
                        from backend.weknora_port.retrieval.search import SearchNothing
                        if isinstance(error, SearchNothing):
                            planned["native_filter_replay"] = {
                                "threshold": 0.3, "top1_fallback_floor": 0.15,
                                "filter_logic": "backend.weknora_port.retrieval.rerank.rerank",
                                "replay_rerank_top_k": 30,
                                "retained_chunk_ids_after_native_filter_and_mmr": [],
                                "retained_count": 0, "target_retained": False,
                                "top1_fallback_applies": False,
                            }
                        else:
                            planned["native_filter_replay"] = {"error_class": type(error).__name__}
                else:
                    planned["native_filter_replay"] = {"status": "NOT_RUN_INVALID_PROVIDER_INDEX_SET"}
            planned["target_candidate"] = case["target_candidate"]
            planned["candidate_map"] = case["candidate_map"]
            atomic_json(RUN_ROOT / "send-manifest.json", manifest)
            print("P34_CASE_RESULT " + json.dumps({
                "case_id": case["case_id"], "request_id": planned["request_id"],
                "status": planned["status"], "http_status": capture.get("http_status"),
                "target_score": planned.get("target_score"), "target_score_rank": planned.get("target_score_rank"),
                "usage": planned.get("usage", {"status": "UNREPORTED"}),
                "elapsed_seconds": elapsed,
            }, ensure_ascii=False))
            await registry.close()

        manifest["status"] = "COMPLETE" if all(c["status"] == "RESPONSE_RECEIVED" for c in plan_cases) else "PARTIAL"
        manifest["completed_at"] = utc_now()
        manifest["real_provider_calls"] = real_calls
        manifest["cases"] = plan_cases
        atomic_json(RUN_ROOT / "send-manifest.json", manifest)
        print("P34_SEND_COMPLETE " + json.dumps({
            "status": manifest["status"], "real_provider_calls": real_calls,
            "raw_response_root": str(RUN_ROOT), "manifest": str(RUN_ROOT / "send-manifest.json")}, ensure_ascii=False))
        return 0 if manifest["status"] == "COMPLETE" else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="Send the four P34-authorized rerank requests once")
    args = parser.parse_args()
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(run(args.send))
    except Exception as error:
        # Do not print provider exception messages, connection strings, or keys.
        from backend.weknora_port.models.types import ProviderError
        if isinstance(error, ProviderError):
            code = error.code
        else:
            message = str(error)
            code = message if re.fullmatch(r"[A-Za-z][A-Za-z0-9_:-]{0,120}", message) else "UNEXPECTED_ERROR_" + type(error).__name__
        frame = traceback.extract_tb(error.__traceback__)[-1] if error.__traceback__ else None
        location = f" at {Path(frame.filename).name}:{frame.lineno}:{frame.name}" if frame else ""
        print("P34_ERROR " + str(code) + location, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

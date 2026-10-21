"""Bounded P29 real-API rerank A/B harness.

`prepare` freezes seven isolated QA sessions and scoped rerank settings without
submitting messages. `query CASE_ID` submits exactly one user request. On any
uncertain message POST the case is left UNKNOWN and cannot be retried here.
Credentials are supplied only by the running APIs; this script never reads or
prints credentials. No Gold answers enter the run plan or request body.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT_DEFAULT = Path("E:/WK/run/p29-20261017")
P25 = Path("E:/WK/run/p25-20261017/runtime")
P27 = Path("E:/WK/run/p27-live-20261017")
P21 = Path("E:/WK/run/p21-20261017/pg-final-v2/http-pg-result.json")
P29_FENCE = Path("E:/WK/run/p29-20261017/fence/open-after.json")
PG_SYSTEM_ID = "7696088403504697382"
PG_DATABASE = "weknora_python_port_20261013"
P29_GRANT_ID = "P29-20261017-SF-300000"
MIN_HEADROOM_BEFORE_QUERY = 36_006
CASE_IDS = ("row-0434-on", "row-0434-off", "row-0959-on", "row-0959-off",
            "row-0958-on", "row-0958-off", "utc-empty")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def atomic_append(record_path: Path, obj: dict[str, Any]) -> None:
    save_json(record_path, obj)


def plan_inputs(p25_api: str, p27_api: str, p25_origin: str | None, p27_origin: str | None,
                p25_evidence: Path, p27_evidence: Path) -> dict[str, Any]:
    s25, s27 = read_json(P25 / "state.json"), read_json(P27 / "state.json")
    p25_queries = read_json(P25 / "query-plan.json")["items"]
    q0434 = next(x for x in p25_queries if x["short_qid"] == "row-0434")
    q0959 = next(x for x in p25_queries if x["short_qid"] == "row-0959")
    p27_analysis = read_json(P27 / "analysis.json")
    q0958 = next(x for x in p27_analysis["cases"] if x["qid"] == "row-0958")
    if int(s25["tenant"]) != 332 or int(s27["tenant"]) != 344:
        raise RuntimeError("P29_SAVED_TENANT_IDENTITY_MISMATCH")
    if q0434["question"] != "Which dataset was introduced in the 'coding time' section of the PPT for training?":
        raise RuntimeError("P29_ROW0434_QUESTION_CHANGED")
    if q0959["question"] != "What are used for long-distance communication as introduced in this slide?":
        raise RuntimeError("P29_ROW0959_QUESTION_CHANGED")
    if q0958["question"] != "What are two examples of reflecting surfaces?":
        raise RuntimeError("P29_ROW0958_QUESTION_CHANGED")
    if q0434.get("document_scope") != [] or q0959.get("document_scope") != []:
        raise RuntimeError("P29_P25_DOCUMENT_SCOPE_NOT_EMPTY")
    if s27.get("cases", {}).get("row-0958", {}).get("document") != q0958["document_id"]:
        raise RuntimeError("P29_ROW0958_DOCUMENT_ID_MISMATCH")
    empty_prior = read_json(P21)
    old_empty_kb = empty_prior.get("empty_kb")
    # The saved P21 empty KB belongs to tenant 339. P29 intentionally provisions
    # a fresh empty KB under tenant 332 through the ordinary API; no old KB is reused.
    origin25 = p25_origin or s25.get("origin") or "http://127.0.0.1:56426"
    origin27 = p27_origin or s27.get("origin") or "http://127.0.0.1:56428"
    cases = []
    def append_pair(qid: str, group: str, tenant: int, base: str, origin: str, kb: str,
                    document_ids: list[str], question: str, evidence: Path) -> None:
        for enabled in (True, False):
            mode = "on" if enabled else "off"
            case_id = f"{qid}-{mode}"
            cases.append({"case_id": case_id, "source_case": qid, "source_group": group,
                          "tenant_id": tenant, "api_base": base.rstrip("/"), "origin": origin,
                          "knowledge_base_id": kb, "expected_document_ids": document_ids,
                          "document_scope": [], "knowledge_base_scope": [kb], "question": question,
                          "enable_rerank": enabled, "session_id": None,
                          "request_id": str(uuid.uuid4()), "status": "NOT_PREPARED",
                          "observer_evidence_dir": str(evidence)})
    append_pair("row-0434", "p25-mml", 332, p25_api, origin25, q0434["kb_id"],
                q0434["expected_document_ids"], q0434["question"], p25_evidence)
    append_pair("row-0959", "p25-mml", 332, p25_api, origin25, q0959["kb_id"],
                q0959["expected_document_ids"], q0959["question"], p25_evidence)
    append_pair("row-0958", "p27-image", 344, p27_api, origin27, q0958["kb"],
                [q0958["document_id"]], q0958["question"], p27_evidence)
    cases.append({"case_id": "utc-empty", "source_case": "p21-utc-empty", "source_group": "new-empty-kb",
                  "tenant_id": 332, "api_base": p25_api.rstrip("/"), "origin": origin25,
                  "knowledge_base_id": None, "expected_document_ids": [], "document_scope": [],
                  "knowledge_base_scope": None, "question": "utc时间转换北京时间", "enable_rerank": True,
                  "session_id": None, "request_id": str(uuid.uuid4()), "status": "NOT_PREPARED",
                  "observer_evidence_dir": str(p25_evidence),
                  "prior_p21_empty_kb_tenant339_not_reused": old_empty_kb})
    return {
        "schema": "p29-rerank-ab-plan/v1", "status": "INPUTS_FROZEN_NOT_PREPARED",
        "created_at": now(), "source_paths": {"p25_state": str(P25 / "state.json"),
            "p25_query_plan": str(P25 / "query-plan.json"), "p27_state": str(P27 / "state.json"),
            "p27_analysis": str(P27 / "analysis.json"), "p21_empty_kb_record": str(P21)},
        "source_sha256": {"p25_state": sha((P25 / "state.json").read_bytes()),
            "p25_query_plan": sha((P25 / "query-plan.json").read_bytes()),
            "p27_state": sha((P27 / "state.json").read_bytes()),
            "p27_analysis": sha((P27 / "analysis.json").read_bytes()), "p21_record": sha(P21.read_bytes())},
        "expected_tenants": {"p25": 332, "p27": 344},
        "p21_saved_empty_kb": {"id": old_empty_kb, "tenant": empty_prior.get("tenant"),
                                "note": "not reused: the saved P21 empty KB belongs to tenant 339"},
        "case_count_cap": 7, "cases": cases,
    }


class RunJournal:
    def __init__(self, out: Path):
        self.path = out / "operations.json"
        self.data = read_json(self.path) if self.path.exists() else {}

    def start(self, op: str, method: str, url: str) -> None:
        if op in self.data:
            raise RuntimeError("P29_DO_NOT_REPEAT_OPERATION_" + op)
        self.data[op] = {"method": method, "url": url, "status": "STARTED", "started_at": now()}
        save_json(self.path, self.data)

    def finish(self, op: str, status: str, response: bytes | None = None, http_status: int | None = None) -> None:
        self.data[op].update(status=status, finished_at=now(), http_status=http_status,
                              response_sha256=sha(response) if response is not None else None)
        save_json(self.path, self.data)


async def request(client: httpx.AsyncClient, method: str, path: str, *, json_body: Any = None,
                  binary: bool = False, journal: RunJournal | None = None, op: str | None = None):
    url = path if path.startswith("http") else client.base_url.join(path)
    if journal and method.upper() not in ("GET", "HEAD"):
        journal.start(op or f"{method}:{path}", method, str(url))
    try:
        response = await client.request(method, path, json=json_body)
    except BaseException:
        if journal and op and op in journal.data:
            journal.finish(op, "UNKNOWN")
        raise
    if journal and op and op in journal.data:
        journal.finish(op, "RESPONSE_RECEIVED", response.content, response.status_code)
    if response.status_code not in (200, 201):
        raise RuntimeError(f"P29_HTTP_{response.status_code}_{method}_{path}")
    return response if binary else response.json()


def api_client(base: str, origin: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base, headers={"Origin": origin}, trust_env=False, timeout=180)


async def safe_preflight(client: httpx.AsyncClient) -> dict[str, Any]:
    ready = await request(client, "GET", "/readyz")
    memory = await request(client, "GET", "/api/v1/memory/settings")
    data = memory.get("data", {})
    if data.get("effective") is not False:
        raise RuntimeError("P29_MEMORY_EFFECTIVE_ENABLED_BLOCKS_CLEAN_AB")
    roles_raw = await request(client, "GET", "/api/v1/settings/model-configuration")
    role_data = roles_raw.get("data", roles_raw)
    roles = [{k: r.get(k) for k in ("role", "provider", "model", "source", "key_configured")}
             for r in (role_data.get("roles", []) if isinstance(role_data, dict) else []) if isinstance(r, dict)]
    if not any(r.get("role") == "rerank" and r.get("model") for r in roles):
        raise RuntimeError("P29_RERANK_ROLE_UNAVAILABLE")
    return {"ready_status": ready.get("status", ready),
            "memory_settings": {k: data.get(k) for k in ("workspace_enabled", "user_enabled", "effective", "write_mode", "item_count")},
            "memory_disabled_for_clean_test": True, "safe_model_roles": roles,
            "model_config_response_sha256": sha(json.dumps(role_data, sort_keys=True, default=str).encode("utf-8"))}


async def prepare(out: Path, p25_api: str, p27_api: str, p25_origin: str | None,
                  p27_origin: str | None, p25_evidence: Path, p27_evidence: Path) -> None:
    if (out / "manifest.json").exists() or (out / "operations.json").exists():
        raise RuntimeError("P29_PREPARE_ALREADY_STARTED_NO_REPLAY")
    manifest = plan_inputs(p25_api, p27_api, p25_origin, p27_origin, p25_evidence, p27_evidence)
    out.mkdir(parents=True, exist_ok=True)
    save_json(out / "manifest.json", manifest)
    journal = RunJournal(out)
    grouped = {"p25": [c for c in manifest["cases"] if c["tenant_id"] == 332],
               "p27": [c for c in manifest["cases"] if c["tenant_id"] == 344]}
    clients: dict[str, httpx.AsyncClient] = {}
    preflight = {}
    try:
        for key, items in grouped.items():
            sample = items[0]
            client = api_client(sample["api_base"], sample["origin"])
            clients[key] = client
            preflight[key] = await safe_preflight(client)
            kbs = (await request(client, "GET", "/api/v1/knowledge-bases")).get("data", [])
            by_id = {x["id"]: x for x in kbs}
            for item in items:
                if item["case_id"] == "utc-empty":
                    continue
                kb = by_id.get(item["knowledge_base_id"])
                if kb is None or int(item["tenant_id"]) != (332 if key == "p25" else 344):
                    raise RuntimeError("P29_EXISTING_KB_SCOPE_PREFLIGHT_FAILED_" + item["case_id"])
                if kb.get("cloud_allowed") is not True:
                    raise RuntimeError("P29_EXISTING_KB_CLOUD_CONSENT_NOT_ENABLED_" + item["case_id"])
        manifest["preflight"] = preflight
        manifest["status"] = "PREPARING_SESSIONS"
        save_json(out / "manifest.json", manifest)

        # Verify the local database identity before any P29 API or DB write.
        from scripts.weknora_port_mvp_real import connection
        conn = await connection()
        try:
            await conn.execute("BEGIN TRANSACTION READ ONLY")
            ident = await (await conn.execute("SELECT system_identifier,current_database() AS db FROM pg_control_system(),(SELECT current_database()) q")).fetchone()
            if str(ident["system_identifier"]) != PG_SYSTEM_ID or ident["db"] != PG_DATABASE:
                raise RuntimeError("P29_SOURCE_DATABASE_IDENTITY_MISMATCH")
            db_identity = {"system_identifier": PG_SYSTEM_ID, "database": PG_DATABASE,
                           "tenant_ids": [332, 344], "session_config_updates": "only newly-created P29 sessions"}
        finally:
            try: await conn.execute("ROLLBACK")
            finally: await conn.close()

        # Only a new isolated empty KB is created. The older P21 KB is tenant 339
        # and is not reused or modified.
        empty = next(c for c in manifest["cases"] if c["case_id"] == "utc-empty")
        client = clients["p25"]
        created = await request(client, "POST", "/api/v1/knowledge-bases",
            json_body={"name": "P29 UTC empty evidence 20261017"}, journal=journal, op="create-empty-kb")
        empty_kb = created.get("data", {}).get("id")
        if not empty_kb:
            raise RuntimeError("P29_EMPTY_KB_CREATE_NO_ID")
        empty["knowledge_base_id"] = empty_kb
        empty["knowledge_base_scope"] = [empty_kb]
        empty["new_empty_kb_created_at"] = now()
        save_json(out / "manifest.json", manifest)
        consent = await request(client, "PATCH", f"/api/v1/knowledge-bases/{empty_kb}/external-processing",
            json_body={"cloud_allowed": True}, journal=journal, op="empty-kb-cloud-consent")
        if consent.get("data", {}).get("cloud_allowed") is not True:
            raise RuntimeError("P29_EMPTY_KB_CLOUD_CONSENT_NOT_CONFIRMED")
        empty["empty_kb_cloud_allowed"] = True
        empty["empty_kb_document_count"] = 0

        for item in manifest["cases"]:
            case_id = item["case_id"]
            client = clients["p27"] if item["tenant_id"] == 344 else clients["p25"]
            # Verify new empty KB is actually empty before creating its session.
            if case_id == "utc-empty":
                docs = (await request(client, "GET", f"/api/v1/knowledge-bases/{item['knowledge_base_id']}/documents")).get("data", [])
                if docs:
                    raise RuntimeError("P29_NEW_EMPTY_KB_CONTAINS_DOCUMENTS")
            title = f"P29 {case_id} isolated A/B"
            session = await request(client, "POST", "/api/v1/sessions", json_body={
                "title": title, "knowledge_base_scope": [item["knowledge_base_id"]], "document_scope": []},
                journal=journal, op=f"create-session-{case_id}")
            session_id = session.get("data", {}).get("id")
            if not session_id:
                raise RuntimeError("P29_SESSION_CREATE_NO_ID_" + case_id)
            item["session_id"] = session_id
            item["session_title"] = title
            item["status"] = "SESSION_CREATED"
            save_json(out / "manifest.json", manifest)
            await update_new_session_config(session_id, item["tenant_id"], [item["knowledge_base_id"]],
                                            item["enable_rerank"])
            item["session_rerank_config_verified"] = item["enable_rerank"]
            messages = await request(client, "GET", f"/api/v1/sessions/{session_id}/messages")
            if messages.get("data"):
                raise RuntimeError("P29_NEW_SESSION_HAS_HISTORY_" + case_id)
            item["pre_query_message_count"] = 0
            item["status"] = "READY"
            save_json(out / "manifest.json", manifest)
        manifest["database_identity"] = db_identity
        manifest["status"] = "READY_NO_QUERY_SENT"
        manifest["prepared_at"] = now()
        save_json(out / "manifest.json", manifest)
        print(json.dumps({"status": manifest["status"], "cases": CASE_IDS,
                          "kb_ids": {c["case_id"]: c["knowledge_base_id"] for c in manifest["cases"]},
                          "sessions": {c["case_id"]: c["session_id"] for c in manifest["cases"]},
                          "model_queries_sent": 0}, ensure_ascii=False))
    except BaseException as exc:
        manifest["status"] = "PREPARE_FAILED_NO_AUTOMATIC_RETRY"
        manifest["prepare_error_type"] = type(exc).__name__
        manifest["prepare_error_code"] = str(exc)[:200] if type(exc).__name__ == "RuntimeError" else None
        manifest["failed_at"] = now()
        save_json(out / "manifest.json", manifest)
        raise
    finally:
        for client in clients.values():
            await client.aclose()


async def update_new_session_config(session_id: str, tenant_id: int, kbs: list[str], enable_rerank: bool) -> None:
    """Update only a just-created P29 session's existing context_config column."""
    from scripts.weknora_port_mvp_real import connection
    conn = await connection()
    try:
        async with conn.transaction():
            await conn.execute("SET TRANSACTION READ WRITE")
            cursor = await conn.execute(
                "SELECT id,tenant_id,subject_id,knowledge_base_scope,document_scope,context_config,rerank_model_id "
                "FROM sessions WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL FOR UPDATE",
                (session_id, tenant_id))
            rows = await cursor.fetchall()
            if len(rows) != 1:
                raise RuntimeError("P29_NEW_SESSION_NOT_FOUND_FOR_CONFIG")
            row = rows[0]
            if row["knowledge_base_scope"] != kbs or row["document_scope"] != []:
                raise RuntimeError("P29_NEW_SESSION_SCOPE_MISMATCH")
            await conn.execute(
                "UPDATE sessions SET context_config=jsonb_set(COALESCE(context_config,'{}'::jsonb),"
                "'{conversation}',COALESCE(context_config->'conversation','{}'::jsonb) || "
                "jsonb_build_object('enable_rerank',%s::boolean),true),updated_at=now() "
                "WHERE id=%s AND tenant_id=%s AND knowledge_base_scope=%s::jsonb AND document_scope='[]'::jsonb",
                (enable_rerank, session_id, tenant_id, json.dumps(kbs)))
            after = await (await conn.execute("SELECT context_config->'conversation'->>'enable_rerank' AS value "
                "FROM sessions WHERE id=%s AND tenant_id=%s", (session_id, tenant_id))).fetchone()
            if not after or after["value"] != ("true" if enable_rerank else "false"):
                raise RuntimeError("P29_NEW_SESSION_RERANK_CONFIG_VERIFY_FAILED")
    finally:
        await conn.close()


def sse_finish(raw: bytes) -> str | None:
    found = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.startswith("data:"):
            continue
        try: obj = json.loads(line[5:].strip())
        except Exception: continue
        choices = obj.get("choices") if isinstance(obj, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict) and choices[0].get("finish_reason") is not None:
            found.append(choices[0]["finish_reason"])
        if isinstance(obj, dict) and obj.get("finish_reason") is not None:
            found.append(obj["finish_reason"])
    return found[-1] if found else None


def assistant_text(messages: dict[str, Any]) -> str:
    vals = []
    for item in messages.get("data", []):
        if item.get("role") != "assistant": continue
        body = item.get("content", "")
        if isinstance(body, str): vals.append(body)
        elif isinstance(body, list): vals.extend(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in body)
    return "\n".join(x for x in vals if x).strip()


async def ledger_for(tenant_id: int, session_id: str) -> dict[str, Any]:
    from scripts.weknora_port_mvp_real import connection
    conn = await connection()
    try:
        async with conn.transaction():
            await conn.execute("SET TRANSACTION READ ONLY")
            ident = await (await conn.execute("SELECT system_identifier,current_database() AS db FROM pg_control_system(),(SELECT current_database()) q")).fetchone()
            if str(ident["system_identifier"]) != PG_SYSTEM_ID or ident["db"] != PG_DATABASE:
                raise RuntimeError("P29_LEDGER_DATABASE_IDENTITY_MISMATCH")
            rows = await (await conn.execute(
                "SELECT call_id,request_id,provider,budget_provider,model,capability,state,reserved_input,"
                "reserved_output,held_tokens,actual_total,known_lower_bound,usage_facts,outcome,finished_at "
                "FROM port_token_calls WHERE tenant_id=%s AND session_id=%s ORDER BY call_id", (tenant_id, session_id)
            )).fetchall()
            rows = json.loads(json.dumps(rows, default=str))
            return {"tenant_id": tenant_id, "session_id": session_id, "call_count": len(rows),
                    "settled_calls": sum(x["state"] == "settled" for x in rows),
                    "unknown_calls": sum(x["state"] == "unknown" for x in rows),
                    "reserved_calls": sum(x["state"] == "reserved" for x in rows),
                    "settled_tokens": sum(x.get("actual_total") or 0 for x in rows if x["state"] == "settled"),
                    "unknown_held_tokens": sum(x.get("held_tokens") or 0 for x in rows if x["state"] == "unknown"),
                    "calls": rows}
    finally:
        await conn.close()


async def budget_headroom() -> dict[str, Any]:
    """Read live SiliconFlow occupancy without taking a write lock or changing rows."""
    if not P29_FENCE.exists():
        raise RuntimeError("P29_OPEN_FENCE_RECEIPT_REQUIRED")
    envelope = read_json(P29_FENCE)
    receipt = envelope.get("data", {})
    if (envelope.get("sha256") is None or receipt.get("schema") != "weknora-p29-fence-receipt/v1"
            or receipt.get("grant_id") != P29_GRANT_ID or receipt.get("action") != "open"
            or receipt.get("commit_confirmed") is not True):
        raise RuntimeError("P29_OPEN_FENCE_RECEIPT_INVALID")
    opened = receipt.get("after", {}).get("month", {})
    if opened.get("limit_tokens") != 2_039_220:
        raise RuntimeError("P29_OPEN_FENCE_LIMIT_MISMATCH")
    from scripts.weknora_port_mvp_real import connection
    conn = await connection()
    try:
        async with conn.transaction():
            await conn.execute("SET TRANSACTION READ ONLY")
            ident = await (await conn.execute("SELECT system_identifier,current_database() AS db FROM pg_control_system(),(SELECT current_database()) q")).fetchone()
            if str(ident["system_identifier"]) != PG_SYSTEM_ID or ident["db"] != PG_DATABASE:
                raise RuntimeError("P29_BUDGET_DATABASE_IDENTITY_MISMATCH")
            month = await (await conn.execute(
                "SELECT account_id,provider,month,limit_tokens,carry_in_tokens,carry_status,carry_source,approval_source,blocked_reason "
                "FROM port_token_budget_months WHERE account_id='siliconflow-owner-main' AND provider='siliconflow' AND month='2026-10-01'"
            )).fetchone()
            if not month or month["limit_tokens"] != 2_039_220 or month["approval_source"] != opened.get("approval_source"):
                raise RuntimeError("P29_LIVE_BUDGET_DOES_NOT_MATCH_OPEN_RECEIPT")
            rows = await (await conn.execute(
                "SELECT state,actual_total,held_tokens FROM port_token_calls "
                "WHERE account_id='siliconflow-owner-main' AND budget_provider='siliconflow' AND month='2026-10-01'"
            )).fetchall()
            if any(r["state"] == "reserved" for r in rows):
                raise RuntimeError("P29_RESERVED_PROVIDER_CALLS_EXIST")
            if any(r["state"] not in ("settled", "unknown", "released") for r in rows):
                raise RuntimeError("P29_INVALID_PROVIDER_CALL_STATE")
            used = sum((r["actual_total"] or 0) if r["state"] == "settled" else
                       (r["held_tokens"] or 0) if r["state"] == "unknown" else 0 for r in rows)
            occupied = used + int(month["carry_in_tokens"] or 0)
            return {"limit_tokens": month["limit_tokens"], "occupied_with_carry": occupied,
                    "headroom": month["limit_tokens"] - occupied, "call_count": len(rows),
                    "unknown_calls": sum(r["state"] == "unknown" for r in rows),
                    "reserved_calls": 0, "snapshot_kind": "READ_ONLY_LIVE_BUDGET_AND_LEDGER"}
    finally:
        await conn.close()


def observer_files(case: dict[str, Any], out: Path) -> list[dict[str, Any]]:
    root = Path(case["observer_evidence_dir"])
    if not root.exists():
        return []
    case_out = out / "cases" / case["case_id"] / "observer"
    found: list[dict[str, Any]] = []
    for folder in ("stages", "captures", "rerank", "provider-requests"):
        for path in (root / folder).glob("*.json"):
            try: data = read_json(path)
            except Exception: continue
            if folder in ("stages", "captures"):
                matches = data.get("session_id") == case["session_id"] and data.get("question") == case["question"]
            elif folder == "rerank":
                matches = data.get("run_id") and data["run_id"] in str(data.get("calls", [{}])[0].get("request_id", ""))
                # rerank's hash-based filename is keyed by run_id. Match via call request id
                # against the matching stage/capture run IDs discovered below when needed.
                matches = matches or (data.get("calls") and data["calls"][0].get("request_id", "").startswith(case.get("run_id", "\0")))
            else:
                matches = data.get("request_id", "").startswith(case.get("run_id", "\0"))
            if matches:
                dest = case_out / folder / path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(path.read_bytes())
                found.append({"kind": folder, "source": str(path), "copy": str(dest), "sha256": sha(path.read_bytes())})
    # Resolve the observer run id from stage/capture by exact session identity,
    # then add rerank/provider files that use opaque run-id/request-id hashes.
    run_ids = set()
    for item in found:
        if item["kind"] in ("stages", "captures"):
            try: run_ids.add(read_json(Path(item["source"])).get("run_id"))
            except Exception: pass
    for folder in ("rerank", "provider-requests"):
        for path in (root / folder).glob("*.json"):
            try: data = read_json(path)
            except Exception: continue
            if (data.get("run_id") in run_ids or any(str(x) == data.get("run_id") for x in run_ids)
                    or any(str(data.get("request_id", "")).startswith(str(rid) + "/") for rid in run_ids if rid)):
                dest = case_out / folder / path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(path.read_bytes())
                found.append({"kind": folder, "source": str(path), "copy": str(dest), "sha256": sha(path.read_bytes())})
    return found


async def query_case(out: Path, case_id: str) -> None:
    manifest_path = out / "manifest.json"
    if not manifest_path.exists(): raise RuntimeError("P29_PREPARE_REQUIRED")
    manifest = read_json(manifest_path)
    if manifest.get("status") != "READY_NO_QUERY_SENT": raise RuntimeError("P29_MANIFEST_NOT_READY")
    if case_id not in CASE_IDS: raise RuntimeError("P29_UNKNOWN_CASE")
    case = next(c for c in manifest["cases"] if c["case_id"] == case_id)
    if case.get("status") != "READY" or not case.get("session_id"): raise RuntimeError("P29_CASE_NOT_READY")
    result_path = out / "cases" / case_id / "result.json"
    if result_path.exists(): raise RuntimeError("P29_CASE_ALREADY_ATTEMPTED_NO_RETRY")
    journal = RunJournal(out)
    headroom_before = await budget_headroom()
    if headroom_before["headroom"] < MIN_HEADROOM_BEFORE_QUERY:
        raise RuntimeError("P29_PROVIDER_HEADROOM_TOO_LOW")
    pre_ledger = await ledger_for(case["tenant_id"], case["session_id"])
    if pre_ledger["call_count"]:
        raise RuntimeError("P29_NEW_SESSION_ALREADY_HAS_TOKEN_LEDGER_ROWS")
    case_dir = out / "cases" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    start = {"case_id": case_id, "status": "STARTED", "started_at": now(),
             "request_id": case["request_id"], "session_id": case["session_id"],
             "knowledge_base_id": case["knowledge_base_id"], "document_scope": [],
             "enable_rerank": case["enable_rerank"], "pre_query_ledger": pre_ledger,
             "provider_budget_before": headroom_before}
    save_json(result_path, start)
    client = api_client(case["api_base"], case["origin"])
    response = None
    error_code = None
    try:
        response = await request(client, "POST", f"/api/v1/sessions/{case['session_id']}/messages",
            json_body={"content": case["question"], "request_id": case["request_id"],
                       "knowledge_base_scope": [case["knowledge_base_id"]], "document_scope": [],
                       "reasoning_effort": "off"}, binary=True, journal=journal, op="query-" + case_id)
        raw = response.content
        (case_dir / "answer.sse").write_bytes(raw)
        messages = await request(client, "GET", f"/api/v1/sessions/{case['session_id']}/messages")
        save_json(case_dir / "messages.json", messages)
        assistant_messages = [m for m in messages.get("data", []) if m.get("role") == "assistant"]
        refs: dict[str, dict[str, Any]] = {}
        for message in assistant_messages:
            for ref in message.get("knowledge_references") or []:
                chunk_id = ref.get("chunk_id") or ref.get("id")
                if chunk_id: refs[chunk_id] = ref
        # Stage/context references are added after observer evidence is collected.
        if case.get("observer_evidence_dir"):
            matches = []
            for folder in ("captures", "stages"):
                for path in (Path(case["observer_evidence_dir"]) / folder).glob("*.json"):
                    try: obj = read_json(path)
                    except Exception: continue
                    if obj.get("session_id") == case["session_id"] and obj.get("question") == case["question"]:
                        matches.append((folder, path, obj))
            for folder, path, obj in matches:
                if folder == "captures":
                    for triple in (obj.get("rankings", {}).get("context") or []):
                        if isinstance(triple, list) and triple: refs.setdefault(triple[0], {"chunk_id": triple[0], "source": "capture.context"})
                else:
                    for stage in obj.get("stages", []):
                        for row in stage.get("merge_results", []):
                            refs.setdefault(row["id"], {"chunk_id": row["id"], "source": "stage.merge"})
        readbacks = []
        for chunk_id, reference in refs.items():
            readback = await request(client, "GET", f"/api/v1/chunks/{chunk_id}")
            row = readback.get("data", {})
            identity = {"chunk_id": row.get("chunk_id"), "knowledge_base_id": row.get("knowledge_base_id"),
                        "document_id": row.get("document_id"), "source_version": row.get("source_version"),
                        "chunk_type": row.get("chunk_type"), "start": row.get("start"), "end": row.get("end"),
                        "text_sha256": sha(str(row.get("text", "")).encode("utf-8")),
                        "text_chars": len(str(row.get("text", ""))), "reference": reference}
            if identity["chunk_id"] != chunk_id or identity["knowledge_base_id"] != case["knowledge_base_id"]:
                raise RuntimeError("P29_CHUNK_READBACK_SCOPE_MISMATCH")
            readbacks.append({"identity": identity, "payload": readback})
        save_json(case_dir / "source-readbacks.json", readbacks)
        post_ledger = await ledger_for(case["tenant_id"], case["session_id"])
        headroom_after = await budget_headroom()
        evidences = observer_files(case, out)
        answer = assistant_text(messages)
        result = {**start, "status": "RECEIVED", "completed_at": now(),
                  "http_status": response.status_code, "sse_sha256": sha(raw), "sse_bytes": len(raw),
                  "finish_reason": sse_finish(raw), "answer": answer, "answer_sha256": sha(answer.encode("utf-8")),
                  "assistant_message_ids": [m.get("id") for m in assistant_messages],
                  "reference_count": len(refs), "source_readback_count": len(readbacks),
                  "source_readback_identities": [x["identity"] for x in readbacks],
                  "observer_artifacts": evidences, "post_query_ledger": post_ledger,
                  "ledger_delta_calls": post_ledger["call_count"] - pre_ledger["call_count"],
                  "provider_budget_after": headroom_after}
        save_json(result_path, result)
        print(json.dumps({"status": "RECEIVED", "case_id": case_id, "http_status": response.status_code,
                          "finish_reason": result["finish_reason"], "source_readback_count": len(readbacks),
                          "ledger_delta_calls": result["ledger_delta_calls"], "unknown_calls": post_ledger["unknown_calls"],
                          "sse_sha256": result["sse_sha256"]}, ensure_ascii=False))
    except BaseException as exc:
        # Once the message POST has been attempted, uncertainty is terminal here.
        if journal.data.get("query-" + case_id, {}).get("status") == "UNKNOWN":
            status = "UNKNOWN_NO_RETRY"
        elif response is None and journal.data.get("query-" + case_id, {}).get("status") == "RESPONSE_RECEIVED":
            status = "RESPONSE_RECEIVED_POSTPROCESS_FAILED_NO_QUERY_RETRY"
        elif response is None and "query-" + case_id in journal.data:
            status = "HTTP_REJECTED_NO_QUERY_RETRY"
        else:
            status = "POSTPROCESS_FAILED_NO_QUERY_RETRY"
        start.update(status=status, failed_at=now(), error_type=type(exc).__name__,
                     error_code=str(exc)[:220] if type(exc).__name__ == "RuntimeError" else None)
        try: start["post_failure_ledger"] = await ledger_for(case["tenant_id"], case["session_id"])
        except Exception: start["post_failure_ledger_status"] = "READ_FAILED"
        save_json(result_path, start)
        raise
    finally:
        await client.aclose()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("prepare", "query"))
    ap.add_argument("case_id", nargs="?", choices=CASE_IDS)
    ap.add_argument("--out-dir", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--p25-api", default="http://127.0.0.1:56425")
    ap.add_argument("--p27-api", default="http://127.0.0.1:56427")
    ap.add_argument("--p25-origin", default=None)
    ap.add_argument("--p27-origin", default=None)
    ap.add_argument("--p25-evidence-dir", type=Path, default=OUT_DEFAULT / "evidence-332")
    ap.add_argument("--p27-evidence-dir", type=Path, default=OUT_DEFAULT / "evidence-344")
    args = ap.parse_args()
    if args.action == "query" and not args.case_id:
        ap.error("query requires exactly one CASE_ID; there is intentionally no run-all command")
    if args.action == "prepare" and args.case_id:
        ap.error("prepare does not take CASE_ID")
    if args.action == "prepare":
        asyncio.run(prepare(args.out_dir, args.p25_api, args.p27_api, args.p25_origin, args.p27_origin,
                            args.p25_evidence_dir, args.p27_evidence_dir), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(query_case(args.out_dir, args.case_id), loop_factory=asyncio.SelectorEventLoop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

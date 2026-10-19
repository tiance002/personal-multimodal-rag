"""Read-only source supplement for the frozen 0434 Top1 replay, never a send."""
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
sys.path.insert(0, str(ROOT))


async def main(args):
    from scripts.local_runtime import environment, load_config
    env = environment(load_config(args.config))  # Never print config/env/DSN.
    for path in reversed(env.get("PYTHONPATH", "").split(os.pathsep)):
        if path and path not in sys.path:
            sys.path.insert(0, path)
    import psycopg
    from psycopg.rows import dict_row
    from backend.weknora_port.models.types import CallContext, TrustedScope
    from backend.weknora_port.storage.postgres import PostgresRetriever, IsolationPolicy
    from backend.weknora_port.storage.knowledge import ScopedKnowledgeRepository

    saved = json.loads(args.saved_upload.read_text(encoding="utf-8-sig"))
    wire = json.loads(args.saved_request.read_text(encoding="utf-8-sig"))
    document = saved["documents"][0]
    tenant_id, kb, document_id = document["tenant_id"], document["knowledge_base_id"], document["id"]
    version = document["metadata"]["source_version"]
    owner = wire["scope"]["owner_id"]
    assert tenant_id == 1 and wire["scope"]["knowledge_base_ids"] == [kb]
    conn = await psycopg.AsyncConnection.connect(env["WEKNORA_PORT_DATABASE_URL"],
        autocommit=True, row_factory=dict_row, connect_timeout=5)
    try:
        await conn.execute("BEGIN TRANSACTION READ ONLY")
        identity = await (await conn.execute("SELECT current_database() AS database, "
            "system_identifier::text,current_setting('transaction_read_only') AS read_only FROM pg_control_system()")).fetchone()
        assert identity == dict(database="weknora_python_port_20261013",
                                system_identifier="7696088403504697382", read_only="on")
        principal = await (await conn.execute("SELECT tenant_id,subject_id FROM port_local_principal WHERE slot='local'")).fetchone()
        assert principal == dict(tenant_id=tenant_id, subject_id=owner), "SAVED_PRINCIPAL_MISMATCH"
        current = await (await conn.execute("SELECT tenant_id,knowledge_base_id,file_hash,metadata->>'source_version' AS version "
            "FROM knowledges WHERE id=%s AND deleted_at IS NULL", (document_id,))).fetchone()
        assert current == dict(tenant_id=tenant_id, knowledge_base_id=kb,
                               file_hash=document["file_hash"], version=version), "SOURCE_VERSION_CHANGED"
        retriever = PostgresRetriever(conn, IsolationPolicy(identity["system_identifier"], frozenset({"OLD_BUSINESS_DENIED"})))
        await retriever.verify_identity()
        async def resolve_tenant(subject):
            assert subject == owner
            return tenant_id
        repo = ScopedKnowledgeRepository(retriever, resolve_tenant)
        context = CallContext("OFFLINE_TOP1_SOURCE_SNAPSHOT", TrustedScope(owner, (kb,), (document_id,)))
        ids = [r["id"] for r in saved["chunks"]]
        rows = await repo.get_chunks(ids, context)
        assert set(rows) == set(ids), "MISSING_SAVED_SOURCE_ROWS"
        for frozen in saved["chunks"]:
            row = rows[frozen["id"]]
            assert row.content == frozen["content"] and row.knowledge_id == document_id
            assert row.chunk_metadata.get("source_version") == frozen["metadata"].get("source_version")
        output = dict(schema="top1-readonly-source-snapshot/v1", status="CURRENT_SOURCE_MATCHES_SAVED_UPLOAD",
            identity=identity, tenant_id=tenant_id, knowledge_base_ids=[kb], document_id=document_id,
            source_version=version, source_sha256=document["file_hash"], rows=[asdict(r) for r in rows.values()],
            provider_sends=0, database_writes=0, not_a_historical_database_snapshot=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        await conn.execute("ROLLBACK")
    finally:
        await conn.close()
    print(json.dumps(dict(status=output["status"], rows=len(rows), database_writes=0,
                          provider_sends=0, output=str(args.output),
                          sha256=hashlib.sha256(args.output.read_bytes()).hexdigest())))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "saved-upload", "saved-request", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(parser.parse_args()))

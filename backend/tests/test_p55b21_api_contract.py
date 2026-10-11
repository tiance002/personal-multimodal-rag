"""Actual OpenAPI input contract; no app startup, DB, secrets or models."""
import json
from pathlib import Path
from fastapi import FastAPI
from backend.app.api.routes import router

def test_memory_snapshot_matches_actual_typed_scope_inputs():
    app=FastAPI();app.include_router(router)
    actual=app.openapi()
    saved=json.loads((Path(__file__).resolve().parents[2]/'contracts/memory.openapi.json').read_text())
    assert saved['info']['version']=='memory/v2-global-scoped'
    for name in ('MemoryScopeIn','MemoryItemIn'):
        assert saved['components']['schemas'][name]==actual['components']['schemas'][name]
    props=actual['components']['schemas']['MemoryScopeIn']['properties']
    assert props['scope_type']['default']=='SCOPED' and props['scope_type']['pattern']=='^(GLOBAL|SCOPED)$'

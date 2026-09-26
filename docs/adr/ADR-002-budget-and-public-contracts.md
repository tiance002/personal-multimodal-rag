# ADR 002 Monthly Cloud Budget and Public Contract Snapshots

**Date:** 2026-09-26  
**Status:** accepted for V1.0 implementation  
**Scope:** optional cloud egress accounting, OpenAPI compatibility, and SSE event compatibility

## Decision

Keep cloud calls disabled by default. When a future cloud answer path is enabled, it must pass the existing global/knowledge-base egress policy and then reserve an estimated monthly cost immediately before the provider call. The reservation is atomic per UTC month, uses a PostgreSQL advisory transaction lock, and is settled with the observed cost, released if no request is sent, or kept at the conservative upper bound when the provider result is unknown. A denied or unavailable budget gate must not silently call the provider.

Freeze the V1.0 public surface in generated `contracts/openapi.json` and `contracts/sse/events.schema.json` plus a small `events.sample.jsonl`. Contract tests compare the checked-in OpenAPI path snapshot, validate SSE fields and sequence values, and reject V2-only capabilities. Frontend SSE consumers resume with `Last-Event-ID` and ignore already-seen sequence numbers.

## Consequences

- `model_calls` records reservation month, reserved/settled micro-units, and reservation state; Agent step/run cost counters are persisted separately.
- Contract changes become visible as snapshot drift instead of silently changing the public API.
- V1.0 still has no enabled cloud provider, MCP, external tools, shell execution, approval workflow, or sandbox; those remain outside the current product boundary.

## Evidence

- `scripts/smoke_budget.py` passed concurrent reserve/deny, settle, release/follow-up behavior against PostgreSQL.
- `scripts/contract_test.py` passed OpenAPI snapshot and SSE schema/sample checks; `var/reports/contract-test.json` records `PASS`.
- `frontend/tests/sse.spec.ts` passed sequence deduplication and `Last-Event-ID` reconnect behavior.

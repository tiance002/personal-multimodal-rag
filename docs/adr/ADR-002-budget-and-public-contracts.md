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


## Explicit synthetic acceptance scope (2026-10-01)

Product cloud routes retain the decision above: PostgreSQL monthly admission,
default zero product budget, global/knowledge-base cloud_allowed and unavailable
gate denial are unchanged. Existing currency-unknown model_calls are NOT treated
as USD, repriced, deleted or overwritten. No schema migration is introduced.

The user separately authorized this local acceptance batch: cumulative at most50
real DeepSeek requests AND1,000,000 input+output tokens, including retries and
ambiguous sends. The initially authorized USD0.50 stop limit was explicitly
withdrawn; money is now audit-only. This exception is confined to an independently
invoked synthetic validation entry, not product routes, arbitrary prompts, other
providers, automatic fallback, subscriptions or a monthly quota increase.

The existing task-2 canonical ledger and stable lock atomically persist request
identity, count and token reservation in one fsync/replace write. No new run/day/
restart receives a fresh quota. Generic SessionAttemptGate cannot reserve from
the tagged validation ledger. Before sending, the adapter enforces the exact
approved synthetic prompt, configured official DeepSeek model, single text user
message, thinking disabled, output512, timeout<=30, official HTTPS endpoint and
no redirects. The runner also imposes a total30-second child-process lifetime.
No private knowledge-base content is used; no raw Langfuse trace is sent.

Input reservation uses the prompt UTF-8 byte length plus256 framing tokens;
output reservation is512. Successful validated provider usage settles token
occupancy; missing/invalid usage, truncation, transport/termination uncertainty
keep the full bound, and every reserved attempt remains consumed. Reported usage
beyond the bound persists a block on further sends. Untagged historical attempts
cannot be reinterpreted or reset. Reinitializing while disabled preserves history.

All new fee audit records explicitly use currency=USD, scale=1,000,000.
Exact rational peak cache-miss/input-output pricing rounds upward to microUSD;
cache hit/miss can refine observed-usage upper-bound audit. Peak prices are taken
from https://api-docs.deepseek.com/quick_start/pricing/ (verified2026-10-01).
An observed-usage cost bound is not a verified debit; actual_charge_verified=false
until authoritative charges are available. No USD stop condition remains.

Acceptance evidence:63 affected offline tests PASS, including24 current validation
contract cases, count/token concurrency, cross-day/restart, legacy denial, usage
settlement, unknown retention, private-prompt rejection, model/output/timeout
guards, generic-gate bypass denial and removed-USD-limit behavior. Added overrun
blocking regression was RED1 FAIL/1 PASS before repair. Contract and diff-check
PASS. These are offline safety results, not live or answer-quality acceptance.


## Frozen six-case synthetic RAG extension (2026-10-01)

The explicitly authorized validation exception additionally registers six exact
synthetic-only Core prompts, stable run/purpose/attempt identities, scope SHA256
94b1e292aadd46f532dc2fc5355f9f42877d1522040c20a0140cb48c2799fcdb and per-case output caps. The
phase cap6 and frozen digest are enforced atomically under the existing canonical
global count/token lock. Durable USD audit reservation precedes provider admission;
pretransport denial releases only its own audit, sent unknown retains its bound.
Validated usage is an observed-cost upper bound, never a verified debit. Product
cloud/monthly defaults and legacy fee records are unchanged. This extension does
not admit arbitrary prompts or give a new quota. Six sends completed; entry closed.

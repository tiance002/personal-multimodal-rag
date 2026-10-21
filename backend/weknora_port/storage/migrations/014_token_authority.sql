-- Explicit Token unit; historical monetary columns/rows are never repurposed.
CREATE TABLE IF NOT EXISTS port_call_slots (
 call_id TEXT PRIMARY KEY, unit TEXT NOT NULL CHECK(unit IN ('tokens','money')),
 tenant_id BIGINT NOT NULL, subject_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS port_token_budget_months (
 account_id TEXT NOT NULL, provider TEXT NOT NULL, month DATE NOT NULL,
 limit_tokens BIGINT NOT NULL CHECK(limit_tokens>=0),
 carry_in_tokens BIGINT NOT NULL CHECK(carry_in_tokens>=0),
 carry_status TEXT NOT NULL CHECK(carry_status IN ('KNOWN','UNKNOWN')),
 carry_source TEXT NOT NULL CHECK(carry_source<>''),
 approval_source TEXT NOT NULL CHECK(approval_source<>''),
 blocked_reason TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(account_id,provider,month)
);
CREATE TABLE IF NOT EXISTS port_token_calls (
 call_id TEXT PRIMARY KEY REFERENCES port_call_slots(call_id),
 request_id TEXT NOT NULL, tenant_id BIGINT NOT NULL, subject_id TEXT NOT NULL,
 session_id TEXT NOT NULL, account_id TEXT NOT NULL, provider TEXT NOT NULL,
 budget_provider TEXT NOT NULL, month DATE NOT NULL,
 fingerprint TEXT NOT NULL, wire_sha256 TEXT NOT NULL, wire_bytes BIGINT NOT NULL,
 model TEXT NOT NULL, model_id TEXT NOT NULL, capability TEXT NOT NULL, endpoint TEXT NOT NULL,
 retrieval_kbs JSONB NOT NULL, origin_kbs JSONB NOT NULL,
 reserved_input BIGINT NOT NULL CHECK(reserved_input>0),
 reserved_output BIGINT NOT NULL CHECK(reserved_output>=0),
 known_lower_bound BIGINT NOT NULL DEFAULT 0 CHECK(known_lower_bound>=0),
 held_tokens BIGINT NOT NULL CHECK(held_tokens>=0),
 actual_total BIGINT CHECK(actual_total>=0), usage_facts JSONB,
 bound_source TEXT NOT NULL, permission_source TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'reserved' CHECK(state IN ('reserved','unknown','settled','released')),
 outcome TEXT NOT NULL DEFAULT 'UNKNOWN', finished_at TIMESTAMPTZ,
 FOREIGN KEY(account_id,budget_provider,month)
  REFERENCES port_token_budget_months(account_id,provider,month),
 CHECK(state<>'settled' OR actual_total IS NOT NULL),
 CHECK(state='released' OR held_tokens>=known_lower_bound)
);
CREATE INDEX IF NOT EXISTS port_token_month_usage ON port_token_calls(account_id,budget_provider,month,state);

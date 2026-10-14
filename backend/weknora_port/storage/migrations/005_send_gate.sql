-- New isolated P1 ledger; semantic budget algorithm from readonly budget.py.
-- No old ledger migration/reset. Explicit carry-in authority is mandatory.
CREATE TABLE IF NOT EXISTS send_budget_months (
 account_id TEXT NOT NULL, month DATE NOT NULL,
 budget_microunits BIGINT NOT NULL CHECK(budget_microunits>=0),
 carry_in_microunits BIGINT NOT NULL CHECK(carry_in_microunits>=0),
 carry_in_status VARCHAR(16) NOT NULL CHECK(carry_in_status IN('KNOWN','UNKNOWN')),
 carry_in_source TEXT NOT NULL CHECK(carry_in_source<>''),
 approval_source TEXT NOT NULL CHECK(approval_source<>''),
 blocked_reason TEXT NOT NULL DEFAULT '',
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(account_id,month)
);
CREATE TABLE IF NOT EXISTS send_calls (
 request_id TEXT PRIMARY KEY,
 tenant_id BIGINT NOT NULL, subject_id TEXT NOT NULL, session_id TEXT NOT NULL,
 account_id TEXT NOT NULL, budget_month DATE NOT NULL,
 provider TEXT NOT NULL, model TEXT NOT NULL, model_id TEXT NOT NULL,
 capability TEXT NOT NULL, endpoint TEXT NOT NULL,
 wire_sha256 VARCHAR(64) NOT NULL, wire_bytes BIGINT NOT NULL,
 output_budget BIGINT,
 retrieval_kbs JSONB NOT NULL, retrieval_documents JSONB NOT NULL,
 origin_kbs JSONB NOT NULL,
 permission_source TEXT NOT NULL, price_source TEXT NOT NULL, estimator_source TEXT NOT NULL,
 reserved_microunits BIGINT NOT NULL CHECK(reserved_microunits>=0),
 actual_microunits BIGINT CHECK(actual_microunits>=0),
 state VARCHAR(16) NOT NULL DEFAULT 'reserved'
   CHECK(state IN('reserved','unknown','settled','released')),
 outcome VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
 usage JSONB,
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 finished_at TIMESTAMPTZ,
 FOREIGN KEY(account_id,budget_month) REFERENCES send_budget_months(account_id,month),
 CHECK(state<>'settled' OR actual_microunits IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_send_budget_month ON send_calls(account_id,budget_month,state);
CREATE INDEX IF NOT EXISTS idx_send_owner ON send_calls(tenant_id,subject_id,request_id);

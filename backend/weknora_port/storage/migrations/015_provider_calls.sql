-- Provider usage and durable no-resend identity only. No account or budget state.
-- Historical send_calls / port_token_calls / UNKNOWN rows remain untouched.
CREATE TABLE IF NOT EXISTS port_provider_calls (
 tenant_id BIGINT NOT NULL, subject_id TEXT NOT NULL,
 call_id TEXT NOT NULL, request_id TEXT NOT NULL, capability TEXT NOT NULL,
 session_id TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
 model_id TEXT NOT NULL, wire_sha256 TEXT NOT NULL,
 outcome TEXT NOT NULL DEFAULT 'UNKNOWN', usage JSONB,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ,
 PRIMARY KEY(tenant_id,subject_id,call_id)
);

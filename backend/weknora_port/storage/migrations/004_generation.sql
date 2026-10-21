-- Local Python lifecycle persistence for approved P5 selection/controlled upgrade.
-- This is not an upstream WeKnora table copy. No model/budget execution here.
-- Apply only after verified isolated physical identity and root003 common schema.

CREATE TABLE IF NOT EXISTS generation_runs (
 run_id TEXT PRIMARY KEY,
 tenant_id BIGINT NOT NULL,
 subject_id TEXT NOT NULL,
 session_id TEXT NOT NULL,
 message_id TEXT NOT NULL,
 payload_sha256 VARCHAR(64) NOT NULL CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
 knowledge_base_ids JSONB NOT NULL,
 document_ids JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(tenant_id,subject_id,session_id,message_id),
 UNIQUE(run_id,tenant_id,subject_id)
);
CREATE INDEX IF NOT EXISTS idx_generation_run_scope
 ON generation_runs(tenant_id,subject_id,session_id,message_id);

CREATE TABLE IF NOT EXISTS generation_attempts (
 attempt_id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL,
 tenant_id BIGINT NOT NULL,
 subject_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL CHECK(ordinal IN(1,2)),
 role VARCHAR(16) NOT NULL CHECK(role IN('cheap','expensive','baseline')),
 provider TEXT NOT NULL,
 model_requested TEXT NOT NULL,
 endpoint TEXT NOT NULL,
 envelope_sha256 VARCHAR(64) NOT NULL,
 prompt_sha256 VARCHAR(64) NOT NULL,
 context_sha256 VARCHAR(64) NOT NULL,
 upgrade_reason VARCHAR(32),
 model_reported TEXT,
 status VARCHAR(16) NOT NULL DEFAULT 'STARTED'
   CHECK(status IN('STARTED','COMPLETED','TRUNCATED','FAILED','CANCELLED')),
 finish_reason TEXT NOT NULL DEFAULT '',
 usage JSONB NOT NULL,
 objective JSONB NOT NULL DEFAULT '[]',
 cost_status VARCHAR(64) NOT NULL DEFAULT 'UNKNOWN',
 error_code VARCHAR(128),
 invalid_handle_count INTEGER NOT NULL DEFAULT 0 CHECK(invalid_handle_count>=0),
 started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 finished_at TIMESTAMPTZ,
 UNIQUE(run_id,ordinal),
 FOREIGN KEY(run_id,tenant_id,subject_id)
   REFERENCES generation_runs(run_id,tenant_id,subject_id)
);
CREATE INDEX IF NOT EXISTS idx_generation_attempt_scope
 ON generation_attempts(tenant_id,subject_id,run_id,ordinal);

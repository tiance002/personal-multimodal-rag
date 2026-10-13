-- Local sole-principal bootstrap and durable pre-model admission wrappers.
-- No login/RBAC or change to upstream RAG/generation algorithms.
CREATE TABLE IF NOT EXISTS port_local_principal (
 slot TEXT PRIMARY KEY CHECK(slot='local'),
 tenant_id INTEGER NOT NULL UNIQUE REFERENCES tenants(id),
 subject_id VARCHAR(512) NOT NULL UNIQUE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS port_turns (
 tenant_id INTEGER NOT NULL, subject_id VARCHAR(512) NOT NULL,
 session_id VARCHAR(36) NOT NULL, client_request_id VARCHAR(36) NOT NULL,
 run_id VARCHAR(36) NOT NULL UNIQUE,
 user_message_id VARCHAR(36) NOT NULL UNIQUE REFERENCES messages(id),
 assistant_message_id VARCHAR(36) NOT NULL UNIQUE REFERENCES messages(id),
 q0 TEXT NOT NULL, payload_sha256 VARCHAR(64) NOT NULL,
 mode VARCHAR(16) NOT NULL DEFAULT 'quick' CHECK(mode='quick'),
 knowledge_base_scope JSONB NOT NULL, document_scope JSONB NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK(status IN('pending','completed','error')),
 error_code VARCHAR(128) NOT NULL DEFAULT '',
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(tenant_id,subject_id,session_id,client_request_id),
 FOREIGN KEY(tenant_id,subject_id,session_id) REFERENCES sessions(tenant_id,subject_id,id)
);
CREATE INDEX IF NOT EXISTS idx_port_turn_assistant ON port_turns(tenant_id,subject_id,session_id,assistant_message_id);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS turn_status VARCHAR(16) NOT NULL DEFAULT 'pending';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS turn_error_code VARCHAR(128) NOT NULL DEFAULT '';

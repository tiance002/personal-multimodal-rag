-- Python local role bindings over the fixed provider/model configuration.
-- Secrets are private persistence fields; no API response serializes roles JSON.
CREATE TABLE IF NOT EXISTS port_model_configuration (
 tenant_id INTEGER NOT NULL,
 subject_id VARCHAR(512) NOT NULL,
 revision BIGINT NOT NULL CHECK(revision>0),
 roles JSONB NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(tenant_id,subject_id)
);
-- Minimal existing privacy invariant; independent of retrieval configuration.
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS cloud_allowed BOOLEAN NOT NULL DEFAULT FALSE;

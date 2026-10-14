-- Fixed WeKnora models schema; exact upstream block followed by local isolation metadata.
CREATE TABLE IF NOT EXISTS models (
    id VARCHAR(64) PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id INTEGER NOT NULL,
    name VARCHAR(255) NOT NULL,
    display_name VARCHAR(255) NOT NULL DEFAULT '',
    type VARCHAR(50) NOT NULL,
    source VARCHAR(50) NOT NULL,
    description TEXT,
    parameters JSONB NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT false,
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE
);  

-- Add indexes for models
CREATE INDEX IF NOT EXISTS idx_models_type ON models(type);
CREATE INDEX IF NOT EXISTS idx_models_source ON models(source);

-- Sole-local-owner immutable physical catalog metadata; roles never overwrite old rows.
ALTER TABLE models ADD COLUMN IF NOT EXISTS subject_id VARCHAR(512) NOT NULL;
ALTER TABLE models ADD COLUMN IF NOT EXISTS role VARCHAR(16) NOT NULL;
ALTER TABLE models ADD COLUMN IF NOT EXISTS identity_sha256 VARCHAR(64) NOT NULL;
ALTER TABLE models ADD COLUMN IF NOT EXISTS physical_sha256 VARCHAR(64) NOT NULL;
CREATE INDEX IF NOT EXISTS port_models_owner ON models(tenant_id,subject_id,id);
CREATE INDEX IF NOT EXISTS port_models_physical ON models(tenant_id,subject_id,physical_sha256);

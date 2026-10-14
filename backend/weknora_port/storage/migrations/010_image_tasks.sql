-- Owner-approved image task durability adaptation. Fixed source enqueues
-- image:multimodal with MaxRetry(3), but its Lite executor is memory-only.
CREATE TABLE port_image_tasks (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id INTEGER NOT NULL,
    subject_id VARCHAR(512) NOT NULL,
    knowledge_base_id VARCHAR(36) NOT NULL,
    knowledge_id VARCHAR(36) NOT NULL,
    source_version VARCHAR(36) NOT NULL,
    config_version INTEGER NOT NULL DEFAULT 1,
    selected_config JSONB NOT NULL,
    state VARCHAR(24) NOT NULL DEFAULT 'pending',
    attempt INTEGER NOT NULL DEFAULT 0,
    max_retry INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claim_token VARCHAR(36),
    lease_until TIMESTAMPTZ,
    outcomes JSONB NOT NULL DEFAULT '{}',
    error_code VARCHAR(80) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, knowledge_base_id, knowledge_id, source_version),
    CHECK (state IN ('pending','processing','retry','completed','failed','cancelled','orphaned','unknown')),
    CHECK (attempt >= 0 AND max_retry = 3)
);
CREATE INDEX port_image_tasks_due ON port_image_tasks(state,next_attempt_at,lease_until);
CREATE INDEX port_image_tasks_scope ON port_image_tasks(tenant_id,subject_id,knowledge_base_id,knowledge_id);

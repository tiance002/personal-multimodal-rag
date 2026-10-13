-- Fixed WeKnora migrations/versioned/000001_agent.up.sql:199-200.
ALTER TABLE knowledges ADD COLUMN IF NOT EXISTS summary_status VARCHAR(32) DEFAULT 'none';
CREATE INDEX IF NOT EXISTS idx_knowledges_summary_status ON knowledges(summary_status);
-- Fixed WeKnora migrations/versioned/000078_chunk_editing_and_custom_metadata.up.sql:9.
ALTER TABLE knowledges ADD COLUMN IF NOT EXISTS custom_metadata JSONB NOT NULL DEFAULT '{}'::JSONB;

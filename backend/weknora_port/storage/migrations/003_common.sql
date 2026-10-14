-- Fixed WeKnora history tables; Python local-owner isolation metadata follows.

CREATE TABLE IF NOT EXISTS tenants (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    retriever_engines JSONB NOT NULL DEFAULT '[]',
    status VARCHAR(50) DEFAULT 'active',
    business VARCHAR(255) NOT NULL,
    storage_quota BIGINT NOT NULL DEFAULT 10737418240, -- 默认10GB配额(Bytes)
    storage_used BIGINT NOT NULL DEFAULT 0, -- 已使用的存储空间(Bytes)
    agent_config JSONB DEFAULT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS sessions (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id INTEGER NOT NULL,
    title VARCHAR(255),
    description TEXT,
    knowledge_base_id VARCHAR(36),
    max_rounds INTEGER NOT NULL DEFAULT 5,
    enable_rewrite BOOLEAN NOT NULL DEFAULT true,
    fallback_strategy VARCHAR(255) NOT NULL DEFAULT 'fixed',
    fallback_response TEXT NOT NULL DEFAULT '很抱歉，我暂时无法回答这个问题。',
    keyword_threshold FLOAT NOT NULL DEFAULT 0.5,
    vector_threshold FLOAT NOT NULL DEFAULT 0.5,
    rerank_model_id VARCHAR(64),
    embedding_top_k INTEGER NOT NULL DEFAULT 10,
    rerank_top_k INTEGER NOT NULL DEFAULT 10,
    rerank_threshold FLOAT NOT NULL DEFAULT 0.65,
    summary_model_id VARCHAR(64),
    summary_parameters JSONB NOT NULL DEFAULT '{}',
    agent_config JSONB DEFAULT NULL,
    context_config JSONB DEFAULT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS messages (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4(),
    request_id VARCHAR(36) NOT NULL,
    session_id VARCHAR(36) NOT NULL,
    role VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    knowledge_references JSONB NOT NULL DEFAULT '[]',
    agent_steps JSONB DEFAULT NULL,
    is_completed BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE
);

-- Migration: 000027_message_rendered_content
-- Description: Add rendered_content column to messages to persist RAG-augmented user content for multi-turn context
DO $$ BEGIN RAISE NOTICE '[Migration 000027] Adding rendered_content column to messages'; END $$;

ALTER TABLE messages ADD COLUMN IF NOT EXISTS rendered_content TEXT NOT NULL DEFAULT '';
COMMENT ON COLUMN messages.rendered_content IS 'Full RAG-augmented user message sent to LLM, preserving retrieval context across turns';

DO $$ BEGIN RAISE NOTICE '[Migration 000027] rendered_content column added to messages successfully'; END $$;


ALTER TABLE messages ADD COLUMN IF NOT EXISTS attachments JSONB DEFAULT '[]'::jsonb;

ALTER TABLE messages ADD COLUMN IF NOT EXISTS usage JSONB;



-- Minimal local principal and immutable Scope provenance; no login/SaaS tables.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS memory_config JSONB;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS subject_id VARCHAR(512) NOT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS knowledge_base_scope JSONB NOT NULL DEFAULT '[]';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS document_scope JSONB NOT NULL DEFAULT '[]';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS tenant_id INTEGER NOT NULL;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS subject_id VARCHAR(512) NOT NULL;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS image_captions JSONB NOT NULL DEFAULT '[]';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS used_memories JSONB;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS knowledge_base_scope JSONB NOT NULL DEFAULT '[]';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS document_scope JSONB NOT NULL DEFAULT '[]';
CREATE UNIQUE INDEX IF NOT EXISTS port_sessions_owner_id ON sessions(tenant_id,subject_id,id);
CREATE INDEX IF NOT EXISTS port_messages_owner_session ON messages(tenant_id,subject_id,session_id,created_at,id);
CREATE UNIQUE INDEX IF NOT EXISTS port_message_request_role ON messages(tenant_id,subject_id,session_id,request_id,role);

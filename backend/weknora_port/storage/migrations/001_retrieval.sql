-- Tencent/WeKnora MIT; fixed commit 3e8b0bfc80b845b2d4b2ed683994748741450a97

-- See docs/weknora-port/UPSTREAM_LICENSE.md. Isolated database only.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE EXTENSION IF NOT EXISTS vector;

CREATE EXTENSION IF NOT EXISTS pg_search;

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    tenant_id INTEGER NOT NULL,
    chunking_config JSONB NOT NULL DEFAULT '{"chunk_size": 512, "chunk_overlap": 50, "split_markers": ["\n\n", "\n", "。"], "keep_separator": true}',
    image_processing_config JSONB NOT NULL DEFAULT '{"enable_multimodal": false, "model_id": ""}',
    embedding_model_id VARCHAR(64) NOT NULL,
    summary_model_id VARCHAR(64) NOT NULL,
    rerank_model_id VARCHAR(64) NOT NULL,
    cos_config JSONB NOT NULL DEFAULT '{}',
    vlm_config JSONB NOT NULL DEFAULT '{}',
    extract_config JSONB NULL DEFAULT NULL,
    auto_tag_config JSONB NULL,
    profile_config JSONB NULL,
    generated_profile JSONB NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS knowledges (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id INTEGER NOT NULL,
    knowledge_base_id VARCHAR(36) NOT NULL,
    type VARCHAR(50) NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    source VARCHAR(2048) NOT NULL,
    parse_status VARCHAR(50) NOT NULL DEFAULT 'unprocessed',
    enable_status VARCHAR(50) NOT NULL DEFAULT 'enabled',
    embedding_model_id VARCHAR(64),
    file_name VARCHAR(255),
    file_type VARCHAR(50),
    file_size BIGINT,
    file_path TEXT,
    file_hash VARCHAR(64),
    storage_size BIGINT NOT NULL DEFAULT 0, -- 存储大小(Byte)
    metadata JSONB,
    profile JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT,
    deleted_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS chunks (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id INTEGER NOT NULL,
    knowledge_base_id VARCHAR(36) NOT NULL,
    knowledge_id VARCHAR(36) NOT NULL,
    content TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT true,
    start_at INTEGER NOT NULL,
    end_at INTEGER NOT NULL,
    pre_chunk_id VARCHAR(36),
    next_chunk_id VARCHAR(36),
    chunk_type VARCHAR(20) NOT NULL DEFAULT 'text',
    parent_chunk_id VARCHAR(36),
    image_info TEXT,
    relation_chunks JSONB,
    indirect_relation_chunks JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS embeddings (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    source_id VARCHAR(64) NOT NULL,
    source_type INTEGER NOT NULL,
    chunk_id VARCHAR(64),
    knowledge_id VARCHAR(64),
    knowledge_base_id VARCHAR(64),
    content TEXT,
    dimension INTEGER NOT NULL,
    embedding halfvec
);

CREATE UNIQUE INDEX IF NOT EXISTS embeddings_unique_source ON embeddings(source_id, source_type);

CREATE INDEX IF NOT EXISTS embeddings_search_idx ON embeddings
USING bm25 (id, knowledge_base_id, content, knowledge_id, chunk_id)
WITH (
    key_field = 'id',
    text_fields = '{
        "content": {
          "tokenizer": {"type": "chinese_lindera"}
        }
    }'
);

ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS is_enabled BOOLEAN DEFAULT TRUE;

ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS tag_id VARCHAR(36);

CREATE INDEX IF NOT EXISTS idx_embeddings_is_enabled ON embeddings(is_enabled);

CREATE INDEX IF NOT EXISTS idx_embeddings_tag_id ON embeddings(tag_id);

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS metadata JSON;

ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS type VARCHAR(32) NOT NULL DEFAULT 'document';

ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS indexing_strategy JSONB;

-- Migration 000078: editable chunks, revision history, and custom document metadata.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS source_content TEXT NOT NULL DEFAULT '';
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS content_revision INT NOT NULL DEFAULT 0;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS index_status VARCHAR(16) NOT NULL DEFAULT 'ready';
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS last_editor_id VARCHAR(64) NOT NULL DEFAULT '';
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS context_header TEXT NOT NULL DEFAULT '';
UPDATE chunks SET source_content = content WHERE source_content = '';

ALTER TABLE knowledges ADD COLUMN IF NOT EXISTS custom_metadata JSONB NOT NULL DEFAULT '{}'::JSONB;

CREATE TABLE IF NOT EXISTS chunk_revisions (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id BIGINT NOT NULL,
    knowledge_base_id VARCHAR(36) NOT NULL,
    knowledge_id VARCHAR(36) NOT NULL,
    chunk_id VARCHAR(36) NOT NULL,
    revision INT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    editor_id VARCHAR(64) NOT NULL DEFAULT '',
    edit_source VARCHAR(16) NOT NULL DEFAULT 'user',
    edited_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chunk_revisions_chunk_revision ON chunk_revisions (chunk_id, revision);
CREATE INDEX IF NOT EXISTS idx_chunk_revisions_tenant_chunk ON chunk_revisions (tenant_id, chunk_id);

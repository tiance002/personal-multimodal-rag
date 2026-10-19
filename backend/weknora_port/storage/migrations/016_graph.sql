-- S6 opt-in, source-bound graph contributions. No source/index/ledger rewrites.
CREATE TABLE IF NOT EXISTS port_graph_evidence (
 id TEXT PRIMARY KEY,
 tenant_id BIGINT NOT NULL,
 owner_id TEXT NOT NULL CHECK (owner_id <> ''),
 knowledge_base_id VARCHAR(36) NOT NULL,
 document_id VARCHAR(36) NOT NULL,
 source_version TEXT NOT NULL CHECK (source_version <> ''),
 chunk_id VARCHAR(36) NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
 content_sha256 CHAR(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
 source_content TEXT NOT NULL CHECK (source_content <> ''),
 start_at INTEGER NOT NULL CHECK (start_at >= 0),
 end_at INTEGER NOT NULL CHECK (end_at >= start_at),
 page_number INTEGER CHECK (page_number > 0),
 extraction_id TEXT NOT NULL CHECK (extraction_id <> ''),
 producer TEXT NOT NULL CHECK (producer <> ''),
 payload JSONB NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id,owner_id,knowledge_base_id,document_id,source_version,chunk_id)
);
CREATE INDEX IF NOT EXISTS port_graph_evidence_scope_idx
 ON port_graph_evidence(tenant_id,owner_id,knowledge_base_id,document_id,source_version);

CREATE TABLE IF NOT EXISTS port_graph_nodes (
 id TEXT PRIMARY KEY,
 evidence_id TEXT NOT NULL REFERENCES port_graph_evidence(id) ON DELETE CASCADE,
 local_key TEXT NOT NULL CHECK (local_key <> ''),
 name TEXT NOT NULL CHECK (name <> ''),
 entity_type TEXT NOT NULL CHECK (entity_type <> ''),
 aliases JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(aliases)='array'),
 is_anchor BOOLEAN NOT NULL DEFAULT FALSE,
 identity_qualifier TEXT NOT NULL DEFAULT '',
 payload JSONB NOT NULL,
 UNIQUE (evidence_id,local_key),
 UNIQUE (id,evidence_id)
);
CREATE INDEX IF NOT EXISTS port_graph_nodes_evidence_idx ON port_graph_nodes(evidence_id);

CREATE TABLE IF NOT EXISTS port_graph_edges (
 id TEXT PRIMARY KEY,
 evidence_id TEXT NOT NULL REFERENCES port_graph_evidence(id) ON DELETE CASCADE,
 source_node_id TEXT NOT NULL,
 target_node_id TEXT NOT NULL,
 predicate TEXT NOT NULL CHECK (predicate <> ''),
 quote TEXT NOT NULL CHECK (quote <> ''),
 quote_start INTEGER NOT NULL CHECK (quote_start >= 0),
 quote_end INTEGER NOT NULL CHECK (quote_end > quote_start),
 temporal_text TEXT NOT NULL DEFAULT '',
 verification TEXT NOT NULL CHECK (verification <> ''),
 payload JSONB NOT NULL,
 FOREIGN KEY (source_node_id,evidence_id) REFERENCES port_graph_nodes(id,evidence_id),
 FOREIGN KEY (target_node_id,evidence_id) REFERENCES port_graph_nodes(id,evidence_id),
 UNIQUE (evidence_id,source_node_id,target_node_id,predicate,quote_start,quote_end)
);
CREATE INDEX IF NOT EXISTS port_graph_edges_evidence_idx ON port_graph_edges(evidence_id);

"""Generate the isolated history schema from the fixed upstream SQL tables."""
import hashlib
import json
import re
from pathlib import Path

REFERENCE = Path(r"E:\RAG-reference\WeKnora-3e8b0bfc")
TARGET = Path(__file__).resolve().parents[1] / "backend/weknora_port/storage/migrations"
SOURCE = "migrations/paradedb/00-init-db.sql"
ADDITIONS = ["000027_message_rendered_content", "000034_add_attachments", "000085_message_usage"]


def generate():
    original = (REFERENCE / SOURCE).read_text(encoding="utf-8")
    parts = ["-- Fixed WeKnora history tables; Python local-owner isolation metadata follows."]
    for name in ("tenants", "sessions", "messages"):
        match = re.search(r"CREATE TABLE IF NOT EXISTS " + name + r" \(.*?\n\);", original, re.S)
        if match is None:
            raise ValueError(name)
        parts.append(match.group())
    inputs = [SOURCE]
    for name in ADDITIONS:
        filename = "migrations/versioned/" + name + ".up.sql"
        parts.append((REFERENCE / filename).read_text(encoding="utf-8"))
        inputs.append(filename)
    parts.append("""
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
""")
    sql = "\n\n".join(parts).strip() + "\n"
    (TARGET / "003_common.sql").write_text(sql, encoding="utf-8", newline="\n")
    manifest = {"upstream_commit": "3e8b0bfc80b845b2d4b2ed683994748741450a97",
                "generated_sha256": hashlib.sha256(sql.encode()).hexdigest(),
                "sources": [{"path": name, "sha256": hashlib.sha256((REFERENCE / name).read_bytes()).hexdigest()} for name in inputs],
                "adaptations": ["local tenant+subject ownership; no authentication business",
                                "immutable message Scope provenance for external-processing permission checks"]}
    (TARGET / "source_manifest_common.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    generate()

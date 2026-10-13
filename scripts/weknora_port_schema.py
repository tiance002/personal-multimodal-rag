"""Generate scoped new-database schema from the fixed upstream Git blobs.

Generation only: this script never connects to a database or applies SQL.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

PIN = "3e8b0bfc80b845b2d4b2ed683994748741450a97"
ROOT = Path(__file__).resolve().parents[1]


def generate(upstream: Path) -> None:
    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(upstream), *args])
    if git("rev-parse", "HEAD").decode().strip() != PIN:
        raise ValueError("FIXED_SOURCE_IDENTITY_MISMATCH")
    base_path = "migrations/paradedb/00-init-db.sql"
    revision_path = "migrations/versioned/000078_chunk_editing_and_custom_metadata.up.sql"
    base = git("show", f"{PIN}:{base_path}").decode("utf-8")
    revisions = git("show", f"{PIN}:{revision_path}").decode("utf-8")
    chunks = ["-- Tencent/WeKnora MIT; fixed commit " + PIN,
              "-- See docs/weknora-port/UPSTREAM_LICENSE.md. Isolated database only.",
              'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";',
              "CREATE EXTENSION IF NOT EXISTS vector;",
              "CREATE EXTENSION IF NOT EXISTS pg_search;"]
    for table in ("knowledge_bases", "knowledges", "chunks", "embeddings"):
        found = re.search(r"CREATE TABLE IF NOT EXISTS " + table + r" \([\s\S]*?\n\);", base)
        if found is None:
            raise ValueError("UPSTREAM_TABLE_NOT_FOUND:" + table)
        chunks.append(found.group())
    for index in ("embeddings_unique_source", "embeddings_search_idx"):
        found = re.search(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS " + index + r"[\s\S]*?;",base)
        if found is None:
            raise ValueError("UPSTREAM_INDEX_NOT_FOUND:" + index)
        chunks.append(found.group())
    # Source migrations 000002 and 000007 add these columns consumed by the
    # fixed PostgreSQL repository; scopes do not require unrelated SaaS tables.
    chunks += ["ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS is_enabled BOOLEAN DEFAULT TRUE;",
               "ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS tag_id VARCHAR(36);",
               "CREATE INDEX IF NOT EXISTS idx_embeddings_is_enabled ON embeddings(is_enabled);",
               "CREATE INDEX IF NOT EXISTS idx_embeddings_tag_id ON embeddings(tag_id);",
               revisions]
    output = ROOT / "backend/weknora_port/storage/migrations/001_retrieval.sql"
    output.parent.mkdir(parents=True,exist_ok=True)
    sql = "\n".join(line.rstrip() for line in "\n\n".join(chunks).splitlines()) + "\n"
    output.write_text(sql,encoding="utf-8",newline="\n")
    manifest = {"upstream":PIN, "sources":{path:git("rev-parse",f"{PIN}:{path}").decode().strip()
               for path in (base_path,revision_path,
               "migrations/versioned/000002_embeddings.up.sql",
               "migrations/versioned/000007_embeddings_tag_id.up.sql")},
               "generated_sha256":hashlib.sha256(output.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
               "execution":"GENERATION_ONLY", "database_writes":0}
    (output.parent / "source_manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    print("SCOPED_SCHEMA_GENERATED tables=4 original_chunks_revision_schema=preserved database_writes=0")


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--upstream",type=Path,required=True)
    generate(parser.parse_args().upstream)

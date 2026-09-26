"""M1 normalized content, retrieval, conversations, runs, and evidence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0002_m1_rag"
down_revision: str | None = "0001_m0_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "document_sections",
        _id(),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("heading", sa.Text(), nullable=False, server_default=""),
        sa.Column("heading_path", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("start_pos", sa.Integer(), nullable=False),
        sa.Column("end_pos", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], name="sections_kb_fk"),
        sa.ForeignKeyConstraint(["document_id", "version_id"], ["document_versions.document_id", "document_versions.id"], name="sections_version_fk"),
        sa.CheckConstraint("start_pos >= 0 AND end_pos >= start_pos", name="sections_span_ck"),
        sa.UniqueConstraint("version_id", "id", name="sections_version_id_ux"),
    )
    op.create_index("sections_version_idx", "document_sections", ["version_id", "start_pos"])

    op.create_table(
        "chunks",
        _id(),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("start_pos", sa.Integer(), nullable=False),
        sa.Column("end_pos", sa.Integer(), nullable=False),
        sa.Column("heading_path", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("chunk_type", sa.Text(), nullable=False, server_default="text"),
        sa.Column("locator", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], name="chunks_kb_fk"),
        sa.ForeignKeyConstraint(["document_id", "version_id"], ["document_versions.document_id", "document_versions.id"], name="chunks_version_fk"),
        sa.ForeignKeyConstraint(["section_id"], ["document_sections.id"], name="chunks_section_fk"),
        sa.CheckConstraint("chunk_index >= 0", name="chunks_index_ck"),
        sa.CheckConstraint("start_pos >= 0 AND end_pos >= start_pos", name="chunks_span_ck"),
        sa.UniqueConstraint("version_id", "chunk_index", name="chunks_version_index_ux"),
        sa.UniqueConstraint("version_id", "id", name="chunks_version_id_ux"),
    )
    op.create_index("chunks_kb_idx", "chunks", ["knowledge_base_id", "version_id", "chunk_index"])
    op.create_index("chunks_sha_idx", "chunks", ["content_sha256"])

    op.create_table(
        "embedding_profiles",
        _id(),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("model_revision", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("distance", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("provider", "model_name", "model_revision", "dimension", "distance", name="embedding_profiles_identity_ux"),
        sa.CheckConstraint("dimension > 0", name="embedding_profiles_dimension_ck"),
    )

    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], name="chunk_embeddings_chunk_fk"),
        sa.ForeignKeyConstraint(["profile_id"], ["embedding_profiles.id"], name="chunk_embeddings_profile_fk"),
    )

    op.create_table(
        "chunk_terms",
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("term", sa.Text(), primary_key=True),
        sa.Column("term_frequency", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], name="chunk_terms_chunk_fk"),
        sa.CheckConstraint("term_frequency > 0", name="chunk_terms_tf_ck"),
    )
    op.create_index("chunk_terms_term_idx", "chunk_terms", ["term"])

    op.create_table(
        "conversations",
        _id(),
        sa.Column("title", sa.Text(), nullable=False, server_default="New conversation"),
        sa.Column("knowledge_base_scope", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("document_scope", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_table(
        "conversation_messages",
        _id(),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], name="messages_conversation_fk"),
        sa.CheckConstraint("role IN ('user','assistant','system')", name="messages_role_ck"),
    )
    op.create_index("messages_conversation_idx", "conversation_messages", ["conversation_id", "created_at"])

    op.create_table(
        "rag_runs",
        _id(),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("knowledge_base_scope", postgresql.JSONB(), nullable=False),
        sa.Column("document_scope", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("q0", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="created"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], name="runs_conversation_fk"),
        sa.CheckConstraint("status IN ('created','running','completed','failed','cancelled')", name="runs_status_ck"),
    )
    op.create_index("runs_created_idx", "rag_runs", ["created_at"])

    op.create_table(
        "retrieval_events",
        _id(),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["rag_runs.id"], name="retrieval_events_run_fk"),
        sa.UniqueConstraint("run_id", "seq", name="retrieval_events_seq_ux"),
    )

    op.create_table(
        "retrieval_hits",
        _id(),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("raw_score", sa.Float(), nullable=True),
        sa.Column("fused_score", sa.Float(), nullable=True),
        sa.Column("sources", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.ForeignKeyConstraint(["run_id"], ["rag_runs.id"], name="retrieval_hits_run_fk"),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], name="retrieval_hits_chunk_fk"),
        sa.UniqueConstraint("run_id", "chunk_id", name="retrieval_hits_run_chunk_ux"),
    )
    op.create_index("retrieval_hits_rank_idx", "retrieval_hits", ["run_id", "rank"])

    op.create_table(
        "answer_evidence",
        _id(),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("quote_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["rag_runs.id"], name="answer_evidence_run_fk"),
        sa.ForeignKeyConstraint(["version_id", "chunk_id"], ["chunks.version_id", "chunks.id"], name="answer_evidence_chunk_version_fk"),
        sa.UniqueConstraint("run_id", "label", name="answer_evidence_label_ux"),
    )

    op.create_table(
        "model_calls",
        _id(),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_microunits", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["rag_runs.id"], name="model_calls_run_fk"),
    )


def downgrade() -> None:
    for table in ["model_calls", "answer_evidence", "retrieval_hits", "retrieval_events", "rag_runs", "conversation_messages", "conversations", "chunk_terms", "chunk_embeddings", "embedding_profiles", "chunks", "document_sections"]:
        op.drop_table(table)
    op.execute("DROP EXTENSION IF EXISTS vector")

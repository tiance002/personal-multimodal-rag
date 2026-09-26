"""M0 core knowledge-base and immutable ingestion tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_m0_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("graph_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cloud_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "kb_active_name_ux",
        "knowledge_bases",
        ["name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "kb_alive_idx",
        "knowledge_bases",
        ["created_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("original_size", sa.BigInteger(), nullable=False),
        sa.Column("active_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], name="documents_kb_fk"),
        sa.CheckConstraint("original_size >= 0", name="documents_original_size_ck"),
    )
    op.create_index(
        "doc_kb_idx",
        "documents",
        ["knowledge_base_id", "updated_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("parser_version", sa.Text(), nullable=False),
        sa.Column("index_status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("graph_status", sa.Text(), nullable=False, server_default="disabled"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("activated_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("normalized_content_key", sa.Text(), nullable=True),
        sa.Column("normalized_content_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column("normalizer_version", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name="document_versions_document_fk"),
        sa.UniqueConstraint("document_id", "version_no", name="document_versions_no_ux"),
        sa.UniqueConstraint("document_id", "id", name="document_versions_document_id_ux"),
        sa.CheckConstraint("version_no > 0", name="document_versions_no_ck"),
        sa.CheckConstraint(
            "index_status IN ('queued','processing','indexing','ready','failed','cancelled')",
            name="document_versions_index_status_ck",
        ),
        sa.CheckConstraint(
            "graph_status IN ('disabled','queued','processing','ready','failed')",
            name="document_versions_graph_status_ck",
        ),
        sa.CheckConstraint(
            "(normalized_content_key IS NULL) = (normalized_content_sha256 IS NULL)",
            name="document_versions_normalized_pair_ck",
        ),
    )
    op.create_index("dv_doc_idx", "document_versions", ["document_id", "version_no"])
    op.create_index("dv_status_idx", "document_versions", ["index_status", "graph_status"])
    op.create_foreign_key(
        "documents_active_version_fk",
        "documents",
        "document_versions",
        ["id", "active_version_id"],
        ["document_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("stage", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("lease_until", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"], name="ingestion_jobs_version_fk"),
        sa.CheckConstraint("job_type IN ('ingest','graph_rebuild')", name="ingestion_jobs_type_ck"),
        sa.CheckConstraint("status IN ('queued','leased','running','succeeded','failed','cancelled')", name="ingestion_jobs_status_ck"),
        sa.CheckConstraint("attempts >= 0", name="ingestion_jobs_attempts_ck"),
        sa.CheckConstraint("max_attempts > 0", name="ingestion_jobs_max_attempts_ck"),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="ingestion_jobs_progress_ck"),
    )
    op.create_index("ij_claim_idx", "ingestion_jobs", ["status", "lease_until", "created_at"])
    op.create_index("ij_version_idx", "ingestion_jobs", ["version_id", "job_type"])


def downgrade() -> None:
    op.drop_index("ij_version_idx", table_name="ingestion_jobs")
    op.drop_index("ij_claim_idx", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_constraint("documents_active_version_fk", "documents", type_="foreignkey")
    op.drop_index("dv_status_idx", table_name="document_versions")
    op.drop_index("dv_doc_idx", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index("doc_kb_idx", table_name="documents")
    op.drop_table("documents")
    op.drop_index("kb_alive_idx", table_name="knowledge_bases")
    op.drop_index("kb_active_name_ux", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")

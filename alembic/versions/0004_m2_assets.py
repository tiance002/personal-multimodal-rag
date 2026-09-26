"""M2 source and derived document assets with version-bound lineage."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_m2_assets"
down_revision: str | None = "0003_m1_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_type", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("derived_from_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("source_locator", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="ready"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], name="assets_kb_fk"),
        sa.ForeignKeyConstraint(["document_id", "version_id"], ["document_versions.document_id", "document_versions.id"], name="assets_version_fk"),
        sa.ForeignKeyConstraint(["derived_from_asset_id"], ["document_assets.id"], name="assets_lineage_fk"),
        sa.CheckConstraint("asset_type IN ('source_image','scanned_page','ocr_text','caption','pdf_page')", name="assets_type_ck"),
        sa.CheckConstraint("status IN ('ready','queued','failed')", name="assets_status_ck"),
        sa.UniqueConstraint("version_id", "id", name="assets_version_id_ux"),
    )
    op.create_index("assets_version_idx", "document_assets", ["version_id", "asset_type"])

    op.create_table(
        "chunk_assets",
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], name="chunk_assets_chunk_fk"),
        sa.ForeignKeyConstraint(["asset_id"], ["document_assets.id"], name="chunk_assets_asset_fk"),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"], name="chunk_assets_version_fk"),
    )


def downgrade() -> None:
    op.drop_table("chunk_assets")
    op.drop_index("assets_version_idx", table_name="document_assets")
    op.drop_table("document_assets")

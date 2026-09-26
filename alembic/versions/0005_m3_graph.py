"""M3 version-bound graph nodes, edges, and edge evidence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_m3_graph"
down_revision: str | None = "0004_m2_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "graph_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_type", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("canonical_key", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], name="graph_nodes_kb_fk"),
        sa.ForeignKeyConstraint(["document_id", "version_id"], ["document_versions.document_id", "document_versions.id"], name="graph_nodes_version_fk"),
        sa.UniqueConstraint("version_id", "id", name="graph_nodes_version_id_ux"),
        sa.UniqueConstraint("version_id", "canonical_key", name="graph_nodes_key_ux"),
    )
    op.create_index("graph_nodes_label_idx", "graph_nodes", ["knowledge_base_id", "canonical_key"])

    op.create_table(
        "graph_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], name="graph_edges_kb_fk"),
        sa.ForeignKeyConstraint(["document_id", "version_id"], ["document_versions.document_id", "document_versions.id"], name="graph_edges_version_fk"),
        sa.ForeignKeyConstraint(["source_node_id"], ["graph_nodes.id"], name="graph_edges_source_fk"),
        sa.ForeignKeyConstraint(["target_node_id"], ["graph_nodes.id"], name="graph_edges_target_fk"),
    )

    op.create_table(
        "graph_edge_evidence",
        sa.Column("edge_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("quote_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("locator", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["edge_id"], ["graph_edges.id"], name="graph_evidence_edge_fk"),
        sa.ForeignKeyConstraint(["version_id", "chunk_id"], ["chunks.version_id", "chunks.id"], name="graph_evidence_chunk_version_fk"),
    )


def downgrade() -> None:
    op.drop_table("graph_edge_evidence")
    op.drop_table("graph_edges")
    op.drop_index("graph_nodes_label_idx", table_name="graph_nodes")
    op.drop_table("graph_nodes")

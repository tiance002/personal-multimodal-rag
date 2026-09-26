"""Prevent duplicate live document identities within one knowledge base."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_doc_filename_ux"
down_revision: str | None = "0010_ingestion_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "documents_kb_filename_ux",
        "documents",
        ["knowledge_base_id", "file_name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("documents_kb_filename_ux", table_name="documents")

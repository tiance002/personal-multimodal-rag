"""Record the chunker revision and selected strategy per immutable version."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_chunk_strategy"
down_revision: str | None = "0011_doc_filename_ux"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("chunker_version", sa.Text(), nullable=False, server_default="legacy/fixed-v1"),
    )
    op.add_column(
        "document_versions",
        sa.Column("chunk_strategy", sa.Text(), nullable=False, server_default="fixed"),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "chunk_strategy")
    op.drop_column("document_versions", "chunker_version")

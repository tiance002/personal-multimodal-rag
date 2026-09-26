"""M1 query indexes kept separate from the normalized content migration."""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_m1_indexes"
down_revision: str | None = "0002_m1_rag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("chunks_current_kb_idx", "chunks", ["knowledge_base_id", "document_id", "version_id", "chunk_index"])


def downgrade() -> None:
    op.drop_index("chunks_current_kb_idx", table_name="chunks")

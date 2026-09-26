"""Add claim tokens for fenced ingestion leases."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_ingestion_leases"
down_revision: str | None = "0009_retrieval_perf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ingestion_jobs", sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ij_claim_token_idx", "ingestion_jobs", ["id", "claim_token"])


def downgrade() -> None:
    op.drop_index("ij_claim_token_idx", table_name="ingestion_jobs")
    op.drop_column("ingestion_jobs", "claim_token")

"""Freeze per-upload interpretation metadata; historical unknowns stay NULL.

The current Document fields cannot reliably reconstruct any old revision,
including the active one (a failed candidate may already have changed them).
No blanket backfill is safe. Existing indexed chunks/citations remain readable.
"""
import sqlalchemy as sa
from alembic import op

revision = "0014_version_source_metadata"
down_revision = "0013_message_run_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_versions", sa.Column("file_name", sa.Text(), nullable=True))
    op.add_column("document_versions", sa.Column("media_type", sa.Text(), nullable=True))
    op.add_column("document_versions", sa.Column("original_size", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    # Retain immutable revision facts across application rollbacks.
    # Removing these columns would discard newly captured source metadata.
    raise RuntimeError("Version source metadata migration is additive and forward-only")

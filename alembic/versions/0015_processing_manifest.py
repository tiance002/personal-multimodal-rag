"""Persist parser diagnostics and independent row proof without rewriting history."""
from alembic import op
import sqlalchemy as sa

revision = '0015_processing_manifest'
down_revision = '0014_version_source_metadata'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('document_versions', sa.Column('processing_manifest_key', sa.Text(), nullable=True))
    op.add_column('document_versions', sa.Column('processing_manifest_sha256', sa.Text(), nullable=True))


def downgrade():
    raise RuntimeError('Forward-only: preserve immutable processing proof manifests')

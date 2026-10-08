"""Add source/context and parent-child identity without rewriting old evidence."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0016_parent_child_chunks'
down_revision = '0015_processing_manifest'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chunks', sa.Column('context_header', sa.Text(), nullable=False, server_default=''))
    op.add_column('chunks', sa.Column('parent_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('chunks', sa.Column('chunk_role', sa.Text(), nullable=False, server_default='child'))
    op.add_column('chunks', sa.Column('index_identity', sa.Text(), nullable=False, server_default='legacy/v1'))
    op.add_column('document_versions', sa.Column('index_identity', sa.Text(), nullable=False, server_default='legacy/v1'))
    op.create_check_constraint('chunks_role_ck', 'chunks', "chunk_role IN ('parent','child')")
    op.create_check_constraint('chunks_parent_role_ck', 'chunks', "chunk_role='child' OR parent_id IS NULL")
    op.create_foreign_key('chunks_parent_version_fk', 'chunks', 'chunks',
                          ['version_id', 'parent_id'], ['version_id', 'id'])
    op.create_index('chunks_parent_idx', 'chunks', ['version_id', 'parent_id'])


def downgrade():
    raise RuntimeError('Forward-only: preserve parent/context and historical index identity')

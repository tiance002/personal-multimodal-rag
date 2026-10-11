"""Add an isolated, rebuildable vector index for long-term memory."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0022_memory_vector_index'
down_revision = '0021_global_scoped_memory'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('memory_subjects', sa.Column(
        'retrieval_mode', sa.Text(), nullable=False, server_default='lexical'))
    op.create_check_constraint(
        'memory_retrieval_mode_ck', 'memory_subjects',
        "retrieval_mode IN ('lexical','semantic','hybrid')")
    op.create_table(
        'memory_item_vectors',
        sa.Column('item_id', sa.Uuid(), sa.ForeignKey('long_term_memory_items.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('profile_fingerprint', sa.Text(), primary_key=True),
        sa.Column('input_version', sa.Text(), nullable=False),
        sa.Column('item_version', sa.Integer(), nullable=False),
        sa.Column('content_hash', sa.Text(), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('dimensions', sa.Integer(), nullable=False),
        sa.Column('vector', postgresql.JSONB(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint('item_version > 0', name='memory_vector_version_ck'),
        sa.CheckConstraint('dimensions > 0', name='memory_vector_dimensions_ck'),
    )
    op.create_index('memory_item_vectors_identity_idx', 'memory_item_vectors',
                    ['profile_fingerprint', 'provider', 'model', 'dimensions'])


def downgrade():
    op.drop_index('memory_item_vectors_identity_idx', table_name='memory_item_vectors')
    op.drop_table('memory_item_vectors')
    op.drop_constraint('memory_retrieval_mode_ck', 'memory_subjects', type_='check')
    op.drop_column('memory_subjects', 'retrieval_mode')

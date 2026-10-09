"""Durable execution leases and model attempts; retain all existing histories."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0018_run_attempt_lifecycle'
down_revision = '0017_embedding_profile_identity'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('rag_run_leases',
        sa.Column('run_id', pg.UUID(), primary_key=True),
        sa.Column('request_hash', sa.CHAR(64), nullable=False),
        sa.Column('owner', pg.UUID(), nullable=False),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('cleanup_pending', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(['run_id'], ['rag_runs.id']),
        sa.CheckConstraint("state IN ('IN_PROGRESS','COMPLETED','FAILED','CANCELLED','UNKNOWN','NOT_SENT')", name='run_lease_state_ck'))
    op.create_table('rag_model_attempts',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('run_id', pg.UUID(), nullable=False),
        sa.Column('owner', pg.UUID(), nullable=False),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('envelope_hash', sa.CHAR(64), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('diagnostics', pg.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('clock_timestamp()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('clock_timestamp()')),
        sa.ForeignKeyConstraint(['run_id'], ['rag_runs.id']),
        sa.UniqueConstraint('run_id','role', name='attempt_run_role_ux'),
        sa.UniqueConstraint('run_id','ordinal', name='attempt_run_ordinal_ux'),
        sa.CheckConstraint("role IN ('chat_cheap','chat_expensive') AND ordinal IN (1,2) AND (ordinal=1 OR role='chat_expensive')", name='attempt_role_ordinal_ck'),
        sa.CheckConstraint("state IN ('NOT_SENT','IN_PROGRESS','COMPLETED','UNKNOWN')", name='attempt_state_ck'))


def downgrade():
    # Never discard durable duplicate/billing evidence during rollback.
    connection = op.get_bind()
    if connection.execute(sa.text('SELECT EXISTS (SELECT 1 FROM rag_run_leases) OR EXISTS (SELECT 1 FROM rag_model_attempts)')).scalar_one():
        raise RuntimeError('LIFECYCLE_DOWNGRADE_HAS_HISTORY')
    op.drop_table('rag_model_attempts')
    op.drop_table('rag_run_leases')

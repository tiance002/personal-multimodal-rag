"""Independent context checkpoints/compaction; preserve Chat ordinal limits."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0019_context_checkpoints'
down_revision = '0018_run_attempt_lifecycle'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('context_compaction_attempts',
        sa.Column('id', sa.CHAR(64), primary_key=True),
        sa.Column('run_id', pg.UUID(), nullable=False),
        sa.Column('conversation_id', pg.UUID(), nullable=False),
        sa.Column('owner', pg.UUID(), nullable=False),
        sa.Column('purpose', sa.Text(), nullable=False, server_default='context_compaction'),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('scope', pg.JSONB(), nullable=False),
        sa.Column('covered', pg.JSONB(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('budget_reservation_id', pg.UUID()),
        sa.Column('diagnostics', pg.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('clock_timestamp()')),
        sa.ForeignKeyConstraint(['run_id'], ['rag_runs.id']),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        sa.ForeignKeyConstraint(['budget_reservation_id'], ['model_calls.id']),
        sa.CheckConstraint("purpose='context_compaction' AND kind IN ('SESSION','LIVE')", name='context_purpose_ck'),
        sa.CheckConstraint("state IN ('IN_PROGRESS','COMPLETED','NOT_SENT','UNKNOWN')", name='context_attempt_state_ck'))
    op.create_table('context_checkpoints',
        sa.Column('attempt_id', sa.CHAR(64), primary_key=True),
        sa.Column('conversation_id', pg.UUID(), nullable=False),
        sa.Column('run_id', pg.UUID(), nullable=False),
        sa.Column('scope', pg.JSONB(), nullable=False),
        sa.Column('covered', pg.JSONB(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('summary_sha256', sa.CHAR(64), nullable=False),
        sa.Column('schema_version', sa.Text(), nullable=False, server_default='context-checkpoint/v1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('clock_timestamp()')),
        sa.ForeignKeyConstraint(['attempt_id'], ['context_compaction_attempts.id']),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        sa.ForeignKeyConstraint(['run_id'], ['rag_runs.id']),
        sa.CheckConstraint("length(summary)>0 AND kind IN ('SESSION','LIVE')", name='context_checkpoint_ck'))
    op.create_index('checkpoint_conversation_created_ix','context_checkpoints',['conversation_id','created_at'])
    op.create_table('context_run_protocol',
        sa.Column('run_id', pg.UUID(), primary_key=True),
        sa.Column('messages', pg.JSONB(), nullable=False),
        sa.Column('sha256', sa.CHAR(64), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['rag_runs.id']))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT EXISTS(SELECT 1 FROM context_compaction_attempts) OR EXISTS(SELECT 1 FROM context_run_protocol)')).scalar_one():
        raise RuntimeError('CONTEXT_DOWNGRADE_HAS_HISTORY')
    op.drop_table('context_run_protocol')
    op.drop_table('context_checkpoints')
    op.drop_table('context_compaction_attempts')

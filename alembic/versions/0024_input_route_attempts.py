"""Separate input classification from the unchanged RAG generation limits."""
from alembic import op
import sqlalchemy as sa

revision = '0024_input_route_attempts'
down_revision = '0023_service_token_policy'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('rag_model_attempts', sa.Column('purpose', sa.Text(), nullable=False, server_default='rag'))
    op.drop_constraint('attempt_run_role_ux', 'rag_model_attempts', type_='unique')
    op.drop_constraint('attempt_run_ordinal_ux', 'rag_model_attempts', type_='unique')
    op.create_unique_constraint('attempt_run_role_purpose_ux', 'rag_model_attempts', ['run_id', 'role', 'purpose'])
    op.create_unique_constraint('attempt_run_ordinal_purpose_ux', 'rag_model_attempts', ['run_id', 'ordinal', 'purpose'])
    op.create_check_constraint('attempt_purpose_ck', 'rag_model_attempts',
        "purpose='rag' OR (purpose='input.route' AND role='chat_cheap' AND ordinal=1)")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM rag_model_attempts WHERE purpose!='rag')")).scalar_one():
        raise RuntimeError('INPUT_ROUTE_DOWNGRADE_HAS_HISTORY')
    op.drop_constraint('attempt_purpose_ck', 'rag_model_attempts', type_='check')
    op.drop_constraint('attempt_run_role_purpose_ux', 'rag_model_attempts', type_='unique')
    op.drop_constraint('attempt_run_ordinal_purpose_ux', 'rag_model_attempts', type_='unique')
    op.create_unique_constraint('attempt_run_role_ux', 'rag_model_attempts', ['run_id', 'role'])
    op.create_unique_constraint('attempt_run_ordinal_ux', 'rag_model_attempts', ['run_id', 'ordinal'])
    op.drop_column('rag_model_attempts', 'purpose')

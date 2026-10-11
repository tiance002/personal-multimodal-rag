"""Formal Global/Scoped identity; existing memory remains Scoped."""
from alembic import op
import sqlalchemy as sa
revision='0021_global_scoped_memory'
down_revision='0020_long_term_memory'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('long_term_memory_items',sa.Column('scope_type',sa.Text(),nullable=False,server_default='SCOPED'))
    op.create_check_constraint('memory_scope_type_ck','long_term_memory_items',"scope_type IN ('GLOBAL','SCOPED')")
    op.create_check_constraint('memory_typed_scope_ck','long_term_memory_items',"""
        (scope_type='GLOBAL' AND scope='{}'::jsonb AND origin='explicit'
          AND kind IN ('profile','preference','interest')) OR
        (scope_type='SCOPED' AND scope ? 'kb' AND scope ? 'documents' AND jsonb_typeof(scope->'kb')='array'
          AND jsonb_array_length(scope->'kb')>0 AND jsonb_typeof(scope->'documents')='array')
    """)
    op.drop_index('memory_active_fact_uq',table_name='long_term_memory_items')
    op.create_index('memory_active_fact_uq','long_term_memory_items',
                    ['subject_id','scope_type','scope_hash','fact_key'],unique=True,postgresql_where=sa.text("status='active'"))
    op.drop_index('memory_scope_idx',table_name='long_term_memory_items')
    op.create_index('memory_scope_idx','long_term_memory_items',['subject_id','scope_type','scope_hash','status'])

def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM long_term_memory_items WHERE scope_type='GLOBAL')
        THEN RAISE EXCEPTION 'GLOBAL_MEMORY_DOWNGRADE_DENIED'; END IF; END $$""")
    op.drop_index('memory_active_fact_uq',table_name='long_term_memory_items')
    op.create_index('memory_active_fact_uq','long_term_memory_items',
                    ['subject_id','scope_hash','fact_key'],unique=True,postgresql_where=sa.text("status='active'"))
    op.drop_index('memory_scope_idx',table_name='long_term_memory_items')
    op.create_index('memory_scope_idx','long_term_memory_items',['subject_id','scope_hash','status'])
    op.drop_constraint('memory_typed_scope_ck','long_term_memory_items',type_='check')
    op.drop_constraint('memory_scope_type_ck','long_term_memory_items',type_='check')
    op.drop_column('long_term_memory_items','scope_type')

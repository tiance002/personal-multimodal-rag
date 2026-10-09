"""Independent local-principal memory; never knowledge chunks or Redis history."""
from alembic import op
revision='0020_long_term_memory'
down_revision='0019_context_checkpoints'
branch_labels=None
depends_on=None

def upgrade():
    op.execute('''CREATE TABLE memory_subjects (
        id uuid PRIMARY KEY, read_enabled boolean NOT NULL DEFAULT false,
        write_mode text NOT NULL DEFAULT 'explicit_only' CHECK(write_mode IN ('explicit_only','auto')),
        created_at timestamptz NOT NULL DEFAULT now())''')
    op.execute('''CREATE TABLE long_term_memory_items (
        id uuid PRIMARY KEY, subject_id uuid NOT NULL REFERENCES memory_subjects(id),
        scope jsonb NOT NULL, scope_hash char(64) NOT NULL, kind text NOT NULL
          CHECK(kind IN ('profile','preference','fact','task','interest')),
        fact_key text NOT NULL CHECK(length(fact_key) BETWEEN 1 AND 120),
        content text NOT NULL CHECK(length(content) BETWEEN 1 AND 300),
        content_hash char(64) NOT NULL, origin text NOT NULL CHECK(origin IN ('explicit','extracted')),
        status text NOT NULL CHECK(status IN ('pending','active','rejected','superseded','deleted')),
        version integer NOT NULL CHECK(version>0), replaces_id uuid REFERENCES long_term_memory_items(id),
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now())''')
    op.execute("CREATE UNIQUE INDEX memory_active_fact_uq ON long_term_memory_items(subject_id,scope_hash,fact_key) WHERE status='active'")
    op.execute('CREATE INDEX memory_scope_idx ON long_term_memory_items(subject_id,scope_hash,status)')
    op.execute('''CREATE TABLE memory_sources (
        id uuid PRIMARY KEY, item_id uuid NOT NULL REFERENCES long_term_memory_items(id),
        run_id uuid REFERENCES rag_runs(id), message_id uuid REFERENCES conversation_messages(id),
        source jsonb NOT NULL, source_hash char(64) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(item_id,source_hash))''')
    op.execute('''CREATE TABLE memory_extraction_jobs (
        id char(64) PRIMARY KEY, subject_id uuid NOT NULL REFERENCES memory_subjects(id),
        conversation_id uuid NOT NULL REFERENCES conversations(id), scope jsonb NOT NULL,
        scope_hash char(64) NOT NULL, sources jsonb NOT NULL, cursor jsonb NOT NULL,
        run_id uuid NOT NULL REFERENCES rag_runs(id), purpose text NOT NULL DEFAULT 'memory_extraction'
          CHECK(purpose='memory_extraction'), provider text NOT NULL, model text NOT NULL,
        state text NOT NULL CHECK(state IN ('PENDING','IN_PROGRESS','COMPLETED','NOT_SENT','UNKNOWN')),
        budget_reservation_id uuid REFERENCES model_calls(id), diagnostics jsonb NOT NULL DEFAULT '{}',
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now())''')
    op.execute('''CREATE TABLE memory_extracted_runs (
        subject_id uuid NOT NULL REFERENCES memory_subjects(id), scope_hash char(64) NOT NULL,
        run_id uuid NOT NULL REFERENCES rag_runs(id), job_id char(64) NOT NULL REFERENCES memory_extraction_jobs(id),
        PRIMARY KEY(subject_id,scope_hash,run_id))''')

def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM long_term_memory_items) OR EXISTS(SELECT 1 FROM memory_extraction_jobs)
        THEN RAISE EXCEPTION 'MEMORY_HISTORY_DOWNGRADE_DENIED'; END IF; END $$""")
    for table in ('memory_extracted_runs','memory_extraction_jobs','memory_sources','long_term_memory_items','memory_subjects'):
        op.drop_table(table)

"""Independent normal-service quota; frozen evaluation histories are untouched."""
from alembic import op
import sqlalchemy as sa
from backend.app.adapters.postgres.service_usage import metadata

revision = '0023_service_token_policy'
down_revision = '0022_memory_vector_index'
branch_labels = None
depends_on = None


def upgrade():
    # Existing incompatible tables must fail; do not silently adopt a schema.
    for table in metadata.sorted_tables:
        table.create(op.get_bind(), checkfirst=False)


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text('SELECT EXISTS (SELECT 1 FROM service_token_reservations)')).scalar_one():
        raise RuntimeError('SERVICE_QUOTA_DOWNGRADE_HAS_HISTORY')
    metadata.drop_all(connection)

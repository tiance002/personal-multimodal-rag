"""Include the effective input-space fingerprint in profile uniqueness.

Preserve profile primary keys, fingerprints and embedding foreign keys. A
downgrade is refused if it would collapse distinct effective input spaces.
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_embedding_profile_identity"
down_revision = "0016_parent_child_chunks"
branch_labels = None
depends_on = None

NAME = "embedding_profiles_identity_ux"
MODEL_COLUMNS = ["provider", "model_name", "model_revision", "dimension", "distance"]
IDENTITY_COLUMNS = MODEL_COLUMNS + ["fingerprint"]


def _check(expected_columns):
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    constraints = {c["name"]: c["column_names"] for c in inspector.get_unique_constraints("embedding_profiles")}
    if constraints.get(NAME) != expected_columns:
        raise RuntimeError("EMBEDDING_PROFILE_UNEXPECTED_UNIQUE_CONSTRAINT")
    columns = {c["name"]: c for c in inspector.get_columns("embedding_profiles")}
    if columns["fingerprint"]["nullable"]:
        raise RuntimeError("EMBEDDING_PROFILE_FINGERPRINT_MUST_BE_NOT_NULL")
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM embedding_profiles WHERE fingerprint IS NULL OR btrim(fingerprint)='')")).scalar_one():
        raise RuntimeError("EMBEDDING_PROFILE_INCOMPLETE_EXISTING_IDENTITY")


def upgrade():
    _check(MODEL_COLUMNS)
    op.drop_constraint(NAME, "embedding_profiles", type_="unique")
    op.create_unique_constraint(NAME, "embedding_profiles", IDENTITY_COLUMNS)
    _check(IDENTITY_COLUMNS)


def downgrade():
    _check(IDENTITY_COLUMNS)
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM embedding_profiles GROUP BY provider, model_name, model_revision, dimension, distance HAVING count(*) > 1)")).scalar_one():
        raise RuntimeError("EMBEDDING_PROFILE_DOWNGRADE_WOULD_COLLAPSE_IDENTITIES")
    op.drop_constraint(NAME, "embedding_profiles", type_="unique")
    op.create_unique_constraint(NAME, "embedding_profiles", MODEL_COLUMNS)

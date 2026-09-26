"""Persist per-step Agent cost counters."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_agent_step_cost"
down_revision: str | None = "0007_m4_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_steps", sa.Column("cost_microunits", sa.BigInteger(), nullable=False, server_default="0"))
    op.create_check_constraint("agent_steps_cost_ck", "agent_steps", "cost_microunits >= 0")


def downgrade() -> None:
    op.drop_constraint("agent_steps_cost_ck", "agent_steps", type_="check")
    op.drop_column("agent_steps", "cost_microunits")

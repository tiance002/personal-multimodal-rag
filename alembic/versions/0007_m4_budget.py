"""M4.5 monthly cloud budget reservation fields on model_calls."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_m4_budget"
down_revision: str | None = "0006_m4_agent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("model_calls", sa.Column("budget_month", sa.Date(), nullable=True))
    op.add_column("model_calls", sa.Column("reserved_cost_microunits", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("model_calls", sa.Column("settled_cost_microunits", sa.BigInteger(), nullable=True))
    op.add_column("model_calls", sa.Column("reservation_state", sa.Text(), nullable=False, server_default="none"))
    op.create_check_constraint(
        "model_calls_reservation_state_ck",
        "model_calls",
        "reservation_state IN ('none','reserved','settled','released','unknown')",
    )
    op.create_check_constraint(
        "model_calls_reserved_cost_ck",
        "model_calls",
        "reserved_cost_microunits >= 0 AND (settled_cost_microunits IS NULL OR settled_cost_microunits >= 0)",
    )
    op.create_index("model_calls_budget_month_idx", "model_calls", ["budget_month", "reservation_state"])


def downgrade() -> None:
    op.drop_index("model_calls_budget_month_idx", table_name="model_calls")
    op.drop_constraint("model_calls_reserved_cost_ck", "model_calls", type_="check")
    op.drop_constraint("model_calls_reservation_state_ck", "model_calls", type_="check")
    op.drop_column("model_calls", "reservation_state")
    op.drop_column("model_calls", "settled_cost_microunits")
    op.drop_column("model_calls", "reserved_cost_microunits")
    op.drop_column("model_calls", "budget_month")

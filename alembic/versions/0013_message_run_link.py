"""Link an assistant message to the frozen run that produced it.

Reopening a historical conversation must be able to re-read the same
citations, so the message row carries an explicit `run_id` link instead of
relying on parsing answer text. The column is nullable to keep existing
user/system rows valid.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_message_run_link"
down_revision: str | None = "0012_chunk_strategy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "messages_run_fk",
        "conversation_messages",
        "rag_runs",
        ["run_id"],
        ["id"],
    )
    op.create_index("messages_run_idx", "conversation_messages", ["run_id"])


def downgrade() -> None:
    op.drop_index("messages_run_idx", table_name="conversation_messages")
    op.drop_constraint("messages_run_fk", "conversation_messages", type_="foreignkey")
    op.drop_column("conversation_messages", "run_id")

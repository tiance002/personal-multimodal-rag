"""Retrieval performance: atomic run-event numbering and an ANN vector index.

Two independent changes that only affect retrieval cost, not retrieval results:

1. `rag_runs.next_event_seq` replaces the `SELECT MAX(seq)+1` scan previously
   used to number SSE events, making event numbering O(1) and race-free.
2. An HNSW index on `chunk_embeddings.embedding` lets `vector_candidates`
   order by the pgvector cosine operator instead of ranking in Python.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_retrieval_perf"
down_revision: str | None = "0008_agent_step_cost"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rag_runs", sa.Column("next_event_seq", sa.Integer(), nullable=False, server_default="1"))
    op.create_check_constraint("runs_next_event_seq_ck", "rag_runs", "next_event_seq >= 1")
    op.execute(
        """
        UPDATE rag_runs r
        SET next_event_seq = COALESCE(
            (SELECT MAX(e.seq) + 1 FROM retrieval_events e WHERE e.run_id = r.id),
            1
        )
        """
    )
    op.execute(
        "CREATE INDEX chunk_embeddings_hnsw_idx ON chunk_embeddings USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS chunk_embeddings_hnsw_idx")
    op.drop_constraint("runs_next_event_seq_ck", "rag_runs", type_="check")
    op.drop_column("rag_runs", "next_event_seq")

"""SPEC-004 KD-15: drop the full-text branch's storage and the unreachable HNSW index.

The confirmatory comparison (SPEC-007 KD-12) measured hybrid against dense-only
on 120 questions: b = 3, c = 20, p = 0.000488, vector-only wins. The full-text
branch is removed, and with it the generated `tsv` column and its GIN index.

The HNSW index goes in the same migration because no query ever reached it —
`vector_stmt` orders by `(distance, id)` and an HNSW index can only order by
the distance operator alone. Dense search was already exact.

**Downgrade restores both**, including the generated column, so the schema is
recoverable; what it cannot restore is the branch that read them.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_chunks_tsv_gin", table_name="chunks")
    op.drop_column("chunks", "tsv")
    op.drop_index("ix_chunks_embedding_hnsw", table_name="chunks")


def downgrade() -> None:
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.add_column(
        "chunks",
        sa.Column(
            "tsv",
            sa.dialects.postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=True,
        ),
    )
    op.create_index("ix_chunks_tsv_gin", "chunks", ["tsv"], postgresql_using="gin")

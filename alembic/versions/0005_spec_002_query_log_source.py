"""SPEC-002: query_log gains `source`.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-02

**Why a column and not a separate table** (SPEC-006 Key decision 16, amendment 7;
SPEC-007 Key decision 5). `query_log` is the authoritative spend ledger, and
`SpendGuard` sums `cost_usd` over every row in the window. With no way to tell
traffic apart, an evaluation run had exactly two options and both were wrong:
write rows and consume the *visitor* ceiling indistinguishably — one 50-question
run is ~$0.65 against a derived daily ceiling of $0.64, so it would take the demo
down to measure how good the demo is — or write none, and make eval spend
invisible to the only ledger the project has. A second table would fork the
ledger and make "what did this cost me" a union query nobody remembers to write.

**Backfilled to `visitor`, and that is a claim about history rather than a
default.** Every row written before this migration came from `POST /query`,
because the API is the only thing that has ever held a `session_factory` in a
deployment. `server_default` is kept afterwards so an unmigrated writer cannot
produce a NULL, and the value is deliberately the one that presses *both*
ceilings — an untagged row is treated as the most constrained kind, never the
least.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "query_log",
        sa.Column("source", sa.Text(), nullable=True, server_default="visitor"),
    )
    op.execute("UPDATE query_log SET source = 'visitor' WHERE source IS NULL")
    op.alter_column("query_log", "source", nullable=False)
    # The daily ceiling filters on this column on every refresh, and the refresh
    # is on the hot path once per TTL. Partial on the value the filter actually
    # selects, since the index exists to serve one predicate.
    op.create_index(
        "ix_query_log_visitor_created_at",
        "query_log",
        ["created_at"],
        postgresql_where=sa.text("source = 'visitor'"),
    )


def downgrade() -> None:
    op.drop_index("ix_query_log_visitor_created_at", table_name="query_log")
    op.drop_column("query_log", "source")

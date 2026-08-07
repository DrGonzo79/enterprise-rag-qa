"""SPEC-005 KD-7 amendment 1: query_log.provisional_verdict.

The header token, stored beside the authoritative one rather than instead of it.

**Why a column and not a derivation.** The failure this amendment fixes was
invisible for exactly one reason: only one verdict existed, so a header that
disagreed with the body left no trace anywhere. A rate of disagreement is what
tells anyone whether the trailing token is doing work, whether a prompt change
made it better or worse, and whether a model swap reintroduced the problem --
and none of that is recoverable from `answer_text` by string-matching, which is
the thing `verdict` was made a column to avoid in the first place (migration
0004).

Nullable with no backfill, deliberately: rows written under prompt v1 have no
header/body distinction to record, and inventing one by copying `verdict` would
manufacture agreement that was never measured. `prompt_version` already
separates the eras.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("query_log", sa.Column("provisional_verdict", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("query_log", "provisional_verdict")

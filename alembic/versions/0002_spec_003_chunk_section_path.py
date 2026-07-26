"""SPEC-003: add chunks.section_path for structured citation display.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("section_path", sa.Text(), nullable=False))


def downgrade() -> None:
    op.drop_column("chunks", "section_path")

"""SPEC-005: query_log gains answer_text, verdict, prompt_version.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-26

Each column is justified by a named failure mode, not by anticipated need:

- verdict — refusal is a charter-scored capability; without this column,
  measuring refusal rate means string-matching answer text.
- answer_text — without it the log records token counts and costs about text
  nobody kept. NOTE: retaining answer text is defensible for this demonstration
  deployment (public corpus, no authentication, no user accounts). A deployment
  with real users needs a retention window, a deletion path, and a decision about
  storing question text at all — see SPEC-005, migration 0004 section.
- prompt_version — attributes a logged answer to the prompt that produced it.

Existing rows are backfilled with explicit sentinels rather than empty strings,
so a pre-SPEC-005 row is never mistaken for a request that produced no answer.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("query_log", sa.Column("answer_text", sa.Text(), nullable=True))
    op.add_column("query_log", sa.Column("verdict", sa.Text(), nullable=True))
    op.add_column("query_log", sa.Column("prompt_version", sa.Text(), nullable=True))
    op.execute(
        "UPDATE query_log SET answer_text = '', verdict = 'unknown', "
        "prompt_version = 'pre-spec-005' "
        "WHERE verdict IS NULL"
    )
    op.alter_column("query_log", "answer_text", nullable=False)
    op.alter_column("query_log", "verdict", nullable=False)
    op.alter_column("query_log", "prompt_version", nullable=False)


def downgrade() -> None:
    op.drop_column("query_log", "prompt_version")
    op.drop_column("query_log", "verdict")
    op.drop_column("query_log", "answer_text")

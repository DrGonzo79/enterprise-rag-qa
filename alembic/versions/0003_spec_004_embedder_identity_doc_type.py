"""SPEC-004: qualify embedder identities; add documents.doc_type.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-26

1. chunks.embedding_model becomes a qualified "provider:model" identity
   (SPEC-004 KD-3). Known limitation, accepted in the spec: rows previously
   ingested with the fake embedder were mislabeled 'text-embedding-3-small'
   (the pipeline defect SPEC-004 fixes) and cannot be distinguished here;
   databases known to hold fake vectors must be re-ingested.
2. documents.doc_type (review amendment 2), backfilled by source_uri pattern
   as a one-time bridge for pre-0003 rows; new rows get it from the loader.

Downgrade strips the 'openai:' prefix from every row carrying it — including
rows that were already qualified before this migration ran.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE chunks SET embedding_model = 'openai:' || embedding_model "
        "WHERE embedding_model NOT LIKE '%:%'"
    )
    op.add_column("documents", sa.Column("doc_type", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE documents SET doc_type = CASE
            WHEN source_uri LIKE '%nvlpubs.nist.gov%' THEN 'standard'
            WHEN source_uri LIKE '%eur-lex%' THEN 'regulation'
            WHEN source_uri LIKE '%publications.europa.eu%' THEN 'regulation'
            WHEN source_uri LIKE '%sec.gov%' THEN 'filing'
            ELSE 'unknown'
        END
        """
    )
    op.alter_column("documents", "doc_type", nullable=False)


def downgrade() -> None:
    op.drop_column("documents", "doc_type")
    op.execute(
        "UPDATE chunks SET embedding_model = substring(embedding_model from 8) "
        "WHERE embedding_model LIKE 'openai:%'"
    )

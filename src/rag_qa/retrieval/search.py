"""Dense search over the corpus.

**One branch, since 2026-08-05.** SPEC-004 shipped two — dense and full-text,
fused with RRF — and SPEC-007's confirmatory comparison measured the pair
against the dense arm alone: b = 3, c = 20, p = 0.000488, vector-only wins.
The full-text branch, the OR fallback, the pruning toggle and the fusion step
were removed under SPEC-004 Key decision 15.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Row, Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_qa.db.models import Chunk, Document
from rag_qa.retrieval.types import RetrievalFilters

CANDIDATE_POOL = 50


@dataclass(frozen=True)
class CandidateRow:
    """One ranked row from a single branch; rank is its list position."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    source_uri: str
    doc_type: str
    section_path: str
    ordinal: int
    text: str


_COLUMNS = (
    Chunk.id,
    Chunk.document_id,
    Document.title,
    Document.source_uri,
    Document.doc_type,
    Chunk.section_path,
    Chunk.ordinal,
    Chunk.text,
)


def _filter_conditions(filters: RetrievalFilters | None) -> list[ColumnElement[bool]]:
    if filters is None:
        return []
    conditions: list[ColumnElement[bool]] = []
    if filters.document_ids is not None:
        conditions.append(Chunk.document_id.in_(filters.document_ids))
    if filters.source_uris is not None:
        conditions.append(Document.source_uri.in_(filters.source_uris))
    if filters.doc_types is not None:
        conditions.append(Document.doc_type.in_(filters.doc_types))
    return conditions


def _candidate(row: Row[Any]) -> CandidateRow:
    return CandidateRow(
        chunk_id=row[0],
        document_id=row[1],
        document_title=row[2],
        source_uri=row[3],
        doc_type=row[4],
        section_path=row[5],
        ordinal=row[6],
        text=row[7],
    )


def vector_stmt(
    query_vector: list[float],
    filters: RetrievalFilters | None = None,
    pool: int = CANDIDATE_POOL,
) -> Select[Any]:
    """The dense statement, exposed so it can be EXPLAINed rather than guessed at.

    Pure extraction from `vector_search` (2026-08-05), no behaviour change: the
    eval scripts need to record *which plan actually ran*, and reconstructing the
    query in a second place is a second chance to differ from it — which is how
    the situation below went unnoticed for four specs.

    **`.order_by(distance, Chunk.id)` is not the statement SPEC-004 specifies**,
    which is `ORDER BY c.embedding <=> :qvec LIMIT 50` with no tie-break, and the
    difference is load-bearing: an HNSW index can order by the distance operator
    alone, so **adding a second sort key makes the index unusable for ordering**
    and the planner falls back to a sequential scan and an explicit sort. Proved
    by EXPLAIN — with the tie-break, `enable_seqscan = off` still yields
    `Limit <- Sort <- Seq Scan`; without it, the same setting yields
    `Limit <- Index Scan[ix_chunks_embedding_hnsw]`.

    The tie-break is left in place. Removing it changes retrieval behaviour and
    is **Proposed, not applied** (SPEC-004 KD-7 amendment 1, CLAUDE.md rule 4).
    """
    distance = Chunk.embedding.cosine_distance(query_vector)
    return (
        select(*_COLUMNS)
        .join(Document, Chunk.document_id == Document.id)
        .where(*_filter_conditions(filters))
        .order_by(distance, Chunk.id)
        .limit(pool)
    )


async def vector_search(
    session: AsyncSession,
    query_vector: list[float],
    filters: RetrievalFilters | None = None,
    pool: int = CANDIDATE_POOL,
) -> list[CandidateRow]:
    """Dense search, cosine distance. **Exact: every candidate scanned and ordered.**

    The `SET LOCAL hnsw.ef_search` that used to open this function is gone with
    the HNSW index it configured (SPEC-004 KD-15). It had been setting a GUC on
    a plan that never read it: `vector_stmt` orders by `(distance, id)`, and an
    HNSW index can only order by the distance operator alone, so no query here
    ever reached the index.
    """
    result = await session.execute(vector_stmt(query_vector, filters, pool))
    return [_candidate(row) for row in result.all()]


async def fetch_embedder_identities(session: AsyncSession) -> set[str]:
    """Distinct embedding_model values across the corpus (SPEC-004 KD-4).

    Corpus-wide on purpose, regardless of filters: one embedder per corpus is
    an invariant, and a partial check would let a mixed corpus hide behind a
    narrow filter.
    """
    result = await session.execute(select(Chunk.embedding_model).distinct())
    return {row[0] for row in result}

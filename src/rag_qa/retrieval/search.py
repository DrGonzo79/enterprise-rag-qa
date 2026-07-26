"""The two branch searches. Each takes its own session so the Retriever can
run them concurrently (SPEC-002 Key decision 5)."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Row, func, select, text
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


async def vector_search(
    session: AsyncSession,
    query_vector: list[float],
    filters: RetrievalFilters | None = None,
    pool: int = CANDIDATE_POOL,
) -> list[CandidateRow]:
    """Dense search over the HNSW index, cosine distance.

    hnsw.ef_search is a session GUC and connections are pooled/recycled, so it
    is applied with SET LOCAL in the same transaction as the search on every
    call — it reverts at commit/rollback and can never leak across the pool
    (review amendment 1). Raised from the default 40 to the pool size so the
    index can actually return `pool` candidates.
    """
    await session.execute(text(f"SET LOCAL hnsw.ef_search = {int(pool)}"))
    distance = Chunk.embedding.cosine_distance(query_vector)
    stmt = (
        select(*_COLUMNS)
        .join(Document, Chunk.document_id == Document.id)
        .where(*_filter_conditions(filters))
        .order_by(distance, Chunk.id)
        .limit(pool)
    )
    result = await session.execute(stmt)
    return [_candidate(row) for row in result.all()]


async def fulltext_search(
    session: AsyncSession,
    query: str,
    filters: RetrievalFilters | None = None,
    pool: int = CANDIDATE_POOL,
) -> list[CandidateRow]:
    """Full-text search over the generated tsv column.

    websearch_to_tsquery never raises on arbitrary user input and keeps
    quoted-phrase support (SPEC-004 Key decision 6). No lexical match is a
    defined outcome: the empty list degrades fusion to vector order.
    """
    ts_query = func.websearch_to_tsquery("english", query)
    rank = func.ts_rank_cd(Chunk.tsv, ts_query)
    stmt = (
        select(*_COLUMNS)
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.tsv.op("@@")(ts_query), *_filter_conditions(filters))
        .order_by(rank.desc(), Chunk.id)
        .limit(pool)
    )
    result = await session.execute(stmt)
    return [_candidate(row) for row in result.all()]


async def fetch_embedder_identities(session: AsyncSession) -> set[str]:
    """Distinct embedding_model values across the corpus (SPEC-004 KD-4).

    Corpus-wide on purpose, regardless of filters: one embedder per corpus is
    an invariant, and a partial check would let a mixed corpus hide behind a
    narrow filter.
    """
    result = await session.execute(select(Chunk.embedding_model).distinct())
    return {row[0] for row in result}

"""The two branch searches. Each takes its own session so the Retriever can
run them concurrently (SPEC-002 Key decision 5)."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Row, cast, func, literal, literal_column, select, text
from sqlalchemy.dialects.postgresql import TSQUERY
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
    """Full-text search over the generated tsv column, with an OR fallback.

    websearch_to_tsquery never raises on arbitrary user input and keeps
    quoted-phrase support (SPEC-004 Key decision 6) — but it **ANDs every content
    term**, and that is why this function needs a second attempt.

    **The corpus is three documents in three registers.** The AI Act legislates
    in *shall*, NIST advises in *should*, the 10-K speaks in *we*. Under a
    conjunction a query cannot satisfy two registers at once, so a question
    spanning documents is close to guaranteed to return nothing — measured at
    zero candidates for 46% of the smoke set and 93% of pilot-1 (SPEC-004 AC-12,
    amendment 5). A multi-document system whose lexical branch cannot match
    across its own documents fails the claim the second branch exists to make.

    **The fallback runs only where the conjunction returned nothing**, so no
    query that works today changes: same session, same filters, same ranking,
    same pool. An empty result remains a defined outcome — when neither form
    matches, fusion degrades to vector order exactly as before.
    """
    rows = await _search_with(session, func.websearch_to_tsquery("english", query), filters, pool)
    if rows:
        return rows
    return await _search_with(session, _any_lexeme_tsquery(query), filters, pool)


def _any_lexeme_tsquery(query: str) -> ColumnElement[Any]:
    """The query's lexemes OR-ed together.

    Built from `to_tsvector` rather than by splitting the string, so stemming,
    stop-word removal and compound handling stay identical to the AND form and
    to the indexed column — a hand-split fallback would silently disagree with
    the index it searches.

    `quote_literal` is **defence against a future text-search configuration, not
    against user input**, and is recorded as such rather than claimed as tested:
    neither `english` nor `simple` emits a lexeme containing a single quote, so
    no query can distinguish this from bare concatenation, and removing it leaves
    every test green (verified by mutation). It stays because it costs nothing
    and because the failure it prevents would be silent — a bare concatenation of
    `a'b | c` casts without error and yields a *different* query.
    """
    lexemes = func.unnest(func.tsvector_to_array(func.to_tsvector("english", query)))
    joined = (
        select(func.string_agg(func.quote_literal(literal_column("lexeme")), literal(" | ")))
        .select_from(lexemes.alias("lexeme"))
        .scalar_subquery()
    )
    # A query of nothing but stop words yields no lexemes and therefore NULL,
    # which casts to a NULL tsquery and matches nothing -- the same defined
    # empty outcome as before, not an error.
    return cast(joined, TSQUERY)


async def _search_with(
    session: AsyncSession,
    ts_query: ColumnElement[Any],
    filters: RetrievalFilters | None,
    pool: int,
) -> list[CandidateRow]:
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

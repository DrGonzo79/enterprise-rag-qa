"""The two branch searches. Each takes its own session so the Retriever can
run them concurrently (SPEC-002 Key decision 5)."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Row,
    Select,
    String,
    cast,
    func,
    literal,
    literal_column,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from rag_qa.db.models import Chunk, Document
from rag_qa.retrieval.types import RetrievalFilters

CANDIDATE_POOL = 50
# Frequency pruning: a lexeme present in more than this fraction of chunks is
# dropped from the OR fallback (SPEC-004 AC-12 amendment 6).
#
# **OFF by default (None), PENDING rather than rejected** (amendment 7). Every
# movement it produced on the 26-question smoke set was a single question in
# one direction or another, and unlike the fallback it has no correctness
# argument that survives without those numbers: an unsatisfiable conjunction is
# a defect whatever the metrics say, while dropping high-frequency lexemes is a
# heuristic justified by its effect. It is decided against the confirmatory set,
# not against 26 questions.
#
# 0.25 remains the value to evaluate, chosen from arithmetic rather than tuned:
# a term in a quarter of the corpus partitions it 1:3 at best -- under one bit
# -- while contributing more OR candidates than any discriminative term.
MAX_LEXEME_CHUNK_FRACTION: float | None = None
PRUNING_CANDIDATE_FRACTION = 0.25


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
    # Whether this row came from the OR fallback rather than the conjunction
    # (SPEC-004 AC-12 amendment 5/6). RECORDED, NEVER FUSED: nothing weights on
    # it, and `rrf_fuse` is asserted to produce identical output either way. It
    # exists so SPEC-007 KD-12 can answer "should a fallback candidate be worth
    # 1/61?" -- a question no system that fails to record the distinction can
    # even pose.
    via_fallback: bool = False


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


def _candidate(row: Row[Any], *, via_fallback: bool = False) -> CandidateRow:
    return CandidateRow(
        chunk_id=row[0],
        document_id=row[1],
        document_title=row[2],
        source_uri=row[3],
        doc_type=row[4],
        section_path=row[5],
        ordinal=row[6],
        text=row[7],
        via_fallback=via_fallback,
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
    """Dense search, cosine distance. **Exact today, not approximate.**

    hnsw.ef_search is a session GUC and connections are pooled/recycled, so it
    is applied with SET LOCAL in the same transaction as the search on every
    call — it reverts at commit/rollback and can never leak across the pool
    (review amendment 1).

    ~~Raised from the default 40 to the pool size so the index can actually
    return `pool` candidates.~~ **That justification is inoperative and is struck
    rather than deleted** (2026-08-05): the GUC is set on a plan that never
    reads it, because no query here reaches the HNSW index — see `vector_stmt`.
    The `SET LOCAL` stays because it costs nothing and becomes load-bearing the
    moment the tie-break is removed; SPEC-004 AC-11, which asserts the GUC is in
    effect, passes and says nothing about whether any index consults it.
    """
    await session.execute(text(f"SET LOCAL hnsw.ef_search = {int(pool)}"))
    result = await session.execute(vector_stmt(query_vector, filters, pool))
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
    return await _search_with(session, _any_lexeme_tsquery(query), filters, pool, via_fallback=True)


def _discriminative_lexemes(query: str) -> ColumnElement[Any]:
    """The query's lexemes, minus those too common in this corpus to discriminate.

    **Computed, never maintained.** The frequency is counted against the indexed
    corpus at query time, so it adapts as documents are added and there is no
    list to keep current — which is the property a hand-maintained stop-list
    lacks, and the reason this is the successor to the fallback rather than the
    rejected option (ii) under another name.
    """
    lexeme: ColumnElement[str] = literal_column("lexeme", String)
    stmt = select(func.string_agg(func.quote_literal(lexeme), literal(" | "))).select_from(
        func.unnest(func.tsvector_to_array(func.to_tsvector("english", query))).alias("lexeme")
    )
    if MAX_LEXEME_CHUNK_FRACTION is None:
        return stmt.scalar_subquery()

    total = select(func.count()).select_from(Chunk).scalar_subquery()
    occurrences = (
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.tsv.op("@@")(cast(func.quote_literal(lexeme), TSQUERY)))
        .scalar_subquery()
    )
    return stmt.where(
        occurrences <= func.ceil(total * literal(MAX_LEXEME_CHUNK_FRACTION))
    ).scalar_subquery()


def _any_lexeme_tsquery(query: str) -> ColumnElement[Any]:
    """The query's discriminative lexemes OR-ed together.

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
    # NULL when the query is all stop words, and NULL when every lexeme it has
    # is too common to discriminate. Both cast to a NULL tsquery and match
    # nothing -- a defined empty outcome that degrades fusion to vector order,
    # not an error. The second case is correct rather than a regression: a query
    # whose every term is corpus-common has no lexical signal, and returning the
    # resulting candidates would be worse than returning none.
    return cast(_discriminative_lexemes(query), TSQUERY)


async def _search_with(
    session: AsyncSession,
    ts_query: ColumnElement[Any],
    filters: RetrievalFilters | None,
    pool: int,
    *,
    via_fallback: bool = False,
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
    return [_candidate(row, via_fallback=via_fallback) for row in result.all()]


async def fetch_embedder_identities(session: AsyncSession) -> set[str]:
    """Distinct embedding_model values across the corpus (SPEC-004 KD-4).

    Corpus-wide on purpose, regardless of filters: one embedder per corpus is
    an invariant, and a partial check would let a mixed corpus hide behind a
    narrow filter.
    """
    result = await session.execute(select(Chunk.embedding_model).distinct())
    return {row[0] for row in result}

"""Branch-search tests from SPEC-004 AC-10 (filter push-down) and AC-11
(ef_search GUC scoping), against the dockerized Postgres."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import (
    DENSE_ONLY_TEXT,
    LEXICAL_ONLY_TEXT,
    QUERY_VECTOR,
    SeededCorpus,
)
from rag_qa.retrieval.search import CANDIDATE_POOL, fetch_embedder_identities, vector_search
from rag_qa.retrieval.types import RetrievalFilters

HNSW_EF_SEARCH_DEFAULT = "40"


# --- branch behavior ----------------------------------------------------------


async def test_vector_search_returns_pool_in_distance_order(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    async with session_factory() as session:
        rows = await vector_search(session, QUERY_VECTOR)

    assert len(rows) == CANDIDATE_POOL
    # Seeded angles increase with list position, so rank 1 is the dense-only chunk
    # and the lexical-only chunk (last angle) is far outside the pool — AC-3's setup.
    assert rows[0].text == DENSE_ONLY_TEXT
    assert all(row.text != LEXICAL_ONLY_TEXT for row in rows)
    assert rows[0].document_title == "Synthetic Regulation"
    assert rows[0].doc_type == "regulation"
    assert rows[0].section_path.startswith("Synthetic Regulation › ")


# --- AC-12(b) amendment 5: the OR fallback ------------------------------------


# --- amendment 6: frequency pruning and fallback provenance -------------------


async def test_embedder_identities_are_corpus_wide(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    async with session_factory() as session:
        identities = await fetch_embedder_identities(session)
    assert identities == {"fake:test-v1"}


# --- AC-10: filters pushed into BOTH branch queries ---------------------------


async def test_vector_filter_by_document_id(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    filing_id = seeded_corpus.document_ids["filing"]
    async with session_factory() as session:
        rows = await vector_search(
            session, QUERY_VECTOR, RetrievalFilters(document_ids=(filing_id,))
        )

    # The filing's 12 chunks all rank below the regulation's top 199 corpus-wide,
    # so a post-filter over a 50-row pool would return ZERO. Push-down returns 12.
    assert len(rows) == 12
    assert {row.document_id for row in rows} == {filing_id}


async def test_vector_filter_by_doc_type_and_source_uri(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    async with session_factory() as session:
        by_type = await vector_search(
            session, QUERY_VECTOR, RetrievalFilters(doc_types=("standard",))
        )
        by_uri = await vector_search(
            session, QUERY_VECTOR, RetrievalFilters(source_uris=("synthetic://standard",))
        )

    assert len(by_type) == 3
    assert {row.doc_type for row in by_type} == {"standard"}
    assert [row.chunk_id for row in by_uri] == [row.chunk_id for row in by_type]


async def test_filters_and_together(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    filing_id = seeded_corpus.document_ids["filing"]
    async with session_factory() as session:
        contradictory = await vector_search(
            session,
            QUERY_VECTOR,
            RetrievalFilters(document_ids=(filing_id,), doc_types=("standard",)),
        )
    assert contradictory == []


# --- AC-11: hnsw.ef_search is SET LOCAL, scoped to the search transaction -----

"""Branch-search tests from SPEC-004 AC-10 (filter push-down) and AC-11
(ef_search GUC scoping), against the dockerized Postgres."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import (
    DENSE_ONLY_TEXT,
    LEXICAL_ONLY_TEXT,
    PROBE_QUERY,
    QUERY_VECTOR,
    SeededCorpus,
)
from rag_qa.retrieval.search import (
    CANDIDATE_POOL,
    fetch_embedder_identities,
    fulltext_search,
    vector_search,
)
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


async def test_fulltext_search_finds_the_lexical_chunk(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    async with session_factory() as session:
        rows = await fulltext_search(session, PROBE_QUERY)

    assert [row.text for row in rows] == [LEXICAL_ONLY_TEXT]


async def test_fulltext_search_survives_unbalanced_query_syntax(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    """websearch_to_tsquery, not to_tsquery: a user typing '6(2' must not 500."""
    async with session_factory() as session:
        rows = await fulltext_search(session, "6(2 AND !!! ')")
    assert isinstance(rows, list)


async def test_fulltext_search_no_lexical_match_returns_empty(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    async with session_factory() as session:
        rows = await fulltext_search(session, "zzzznonexistentterm")
    assert rows == []


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


async def test_fulltext_filter_excludes_the_matching_document(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    filing_id = seeded_corpus.document_ids["filing"]
    async with session_factory() as session:
        rows = await fulltext_search(
            session, PROBE_QUERY, RetrievalFilters(document_ids=(filing_id,))
        )
    assert rows == []  # the only lexical match lives in the regulation


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


async def test_ef_search_in_effect_during_search_then_reverts_on_pooled_connection(
    seeded_corpus: SeededCorpus,
) -> None:
    """A single-connection pool guarantees the second session gets the SAME
    physical backend, so a leaked GUC would be visible rather than lucky."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from conftest import DATABASE_URL

    engine = create_async_engine(DATABASE_URL, pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            backend_pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            await vector_search(session, QUERY_VECTOR)
            during = (await session.execute(text("SHOW hnsw.ef_search"))).scalar_one()
            assert during == str(CANDIDATE_POOL)

        # New session -> connection returned to and drawn again from the pool.
        async with factory() as session:
            after = (await session.execute(text("SHOW hnsw.ef_search"))).scalar_one()
            reused_pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
    finally:
        await engine.dispose()

    assert reused_pid == backend_pid, "expected the pooled connection to be reused"
    assert after == HNSW_EF_SEARCH_DEFAULT, "SET LOCAL leaked across pool recycling"

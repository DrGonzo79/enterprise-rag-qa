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


# --- AC-12(b) amendment 5: the OR fallback ------------------------------------


async def test_a_working_conjunction_is_left_alone(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    """The fallback must change nothing that already works, or the branch's
    precision drops on every query rather than only on the silent ones.

    **The query has to be multi-term.** An earlier version of this test used
    `quarklebit` alone, and a single-term OR is identical to a single-term AND —
    so it passed while a mutant that skipped the conjunction entirely and always
    fell back went undetected. `quarklebit govern` separates them: 1 chunk under
    AND, 3 under OR.
    """
    async with session_factory() as session:
        rows = await fulltext_search(session, "quarklebit govern")
    assert [row.text for row in rows] == [LEXICAL_ONLY_TEXT], (
        "the conjunction matched one chunk; a wider result means the fallback ran"
    )


async def test_a_sentence_that_ands_to_nothing_falls_back_and_finds_it(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    """The defect, at synthetic scale. Every content word here is in the corpus
    except `say`, and under a conjunction one absent word empties the result --
    which is how a three-register corpus makes cross-document questions
    unanswerable (SPEC-004 AC-12, amendment 5)."""
    sentence = "What does the quarklebit provision say about exceptional derogation?"
    async with session_factory() as session:
        conjunction = await session.execute(
            text("SELECT count(*) FROM chunks WHERE tsv @@ websearch_to_tsquery('english', :q)"),
            {"q": sentence},
        )
        assert conjunction.scalar_one() == 0, "premise: the AND form finds nothing"
        rows = await fulltext_search(session, sentence)

    assert rows, "the fallback returned nothing"
    assert LEXICAL_ONLY_TEXT in [row.text for row in rows]


async def test_the_fallback_still_pushes_filters_into_the_query(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    """AC-10 must hold on the second attempt too. A fallback that dropped the
    predicate would leak chunks from documents the caller excluded -- and it
    would do so only on the queries that reach the fallback, so it would look
    correct in every test that used a matching query."""
    filing_id = seeded_corpus.document_ids["filing"]
    # Every lexeme here survives frequency pruning: `passage` and `obligations`
    # are in ~199 of 215 chunks and would be dropped, leaving one document.
    # `quarklebit` (1 chunk), `manufacturing` (filing) and `govern` (standard)
    # are rare enough to survive and reach three documents between them.
    sentence = "What does manufacturing say about quarklebit govern?"
    async with session_factory() as session:
        unfiltered = await fulltext_search(session, sentence, pool=250)
        filtered = await fulltext_search(
            session, sentence, RetrievalFilters(document_ids=(filing_id,)), pool=250
        )

    # The premise the assertion needs: without the filter the fallback reaches
    # more than one document, so restricting to one is a real restriction rather
    # than a description of what it would have returned anyway.
    assert len({row.document_id for row in unfiltered}) > 1, (
        "premise: the unfiltered fallback spans documents"
    )
    assert filtered, "the filtered fallback returned nothing"
    assert {row.document_id for row in filtered} == {filing_id}


# --- amendment 6: frequency pruning and fallback provenance -------------------


async def test_a_corpus_common_lexeme_is_pruned_from_the_fallback(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    """`passage` is in 213 of 215 chunks; `quarklebit` is in one. Without pruning
    the OR set returns essentially the whole corpus and the rare term is buried.
    Pruning is what makes the fallback's ranking mean something."""
    async with session_factory() as session:
        common_only = await session.execute(
            text("SELECT count(*) FROM chunks WHERE tsv @@ 'passag'::tsquery")
        )
        assert common_only.scalar_one() > 0.25 * seeded_corpus.total_chunks, (
            "premise: `passage` is above the pruning threshold"
        )
        rows = await fulltext_search(
            session, "What does the passage say about quarklebit?", pool=250
        )

    # `say` is absent so the conjunction is empty; `passage` is pruned; only
    # `quarklebit` survives, so exactly the chunk containing it comes back.
    assert [row.text for row in rows] == [LEXICAL_ONLY_TEXT]


async def test_a_query_of_only_common_lexemes_returns_nothing(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    """Correct, not a regression. A query whose every term is corpus-common has
    no lexical signal at all, and returning the resulting candidates would be
    worse than returning none — AC-12(b) already defines empty as a valid
    outcome that degrades fusion to vector order."""
    async with session_factory() as session:
        assert await fulltext_search(session, "passage obligations zzzabsent") == []


async def test_fallback_rows_are_marked_and_conjunction_rows_are_not(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    """The branch owes its caller `found by conjunction` versus `found by
    fallback`. Recorded now so SPEC-007 KD-12 has the data when it settles
    whether a fallback candidate should be worth 1/61."""
    async with session_factory() as session:
        conjunction = await fulltext_search(session, PROBE_QUERY)
        fallback = await fulltext_search(
            session, "What does the passage say about quarklebit?", pool=250
        )

    assert conjunction and all(not row.via_fallback for row in conjunction)
    assert fallback and all(row.via_fallback for row in fallback)


async def test_a_query_of_only_stop_words_is_empty_not_an_error(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    """`to_tsvector` yields no lexemes, so the aggregate is NULL and the cast is
    a NULL tsquery. That must match nothing rather than raise: an empty result is
    a defined outcome and fusion degrades to vector order."""
    async with session_factory() as session:
        assert await fulltext_search(session, "the and of") == []


async def test_the_fallback_escapes_lexemes_from_hostile_input(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    """Arbitrary punctuation reaching the fallback must not raise. These inputs
    get there precisely because they match nothing under the conjunction.

    **What this does NOT prove: that `quote_literal` is doing anything.** No
    PostgreSQL text-search configuration tried (`english`, `simple`) emits a
    lexeme containing a single quote — the parser strips apostrophes — so no
    input to this function can distinguish escaped concatenation from bare
    concatenation. Verified by mutation: removing `quote_literal` leaves every
    test here green. The escaping is kept as defence against a future
    configuration change, and is recorded as untestable-by-construction rather
    than filed as covered (CLAUDE.md rule 3).
    """
    for hostile in ("' | 'x", "quarklebit' & !'", "a'b c\\d", "!!! ') (&|"):
        async with session_factory() as session:
            assert isinstance(await fulltext_search(session, hostile), list)


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

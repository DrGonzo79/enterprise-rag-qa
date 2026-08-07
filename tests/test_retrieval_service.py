"""Retriever tests from SPEC-004 AC-1, AC-3, AC-4, AC-7, AC-8(CI), AC-9,
AC-11 and AC-12, plus migration 0003 (AC-5)."""

import asyncio
import logging
import statistics
import time
import uuid
from typing import Any

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from conftest import (
    DATABASE_URL,
    PROBE_QUERY,
    SeededChunk,
    SeededCorpus,
    StubQueryEmbedder,
    drop_documents,
    seed_corpus,
)
from rag_qa.retrieval import Retriever
from rag_qa.retrieval.search import CandidateRow
from rag_qa.retrieval.types import (
    EmbedderMismatchError,
    EmptyCorpusError,
    RetrievalFilters,
)


def _retriever(
    session_factory: async_sessionmaker[AsyncSession],
    embedder: StubQueryEmbedder | None = None,
) -> tuple[Retriever, StubQueryEmbedder]:
    embedder = embedder if embedder is not None else StubQueryEmbedder()
    return Retriever(session_factory, embedder), embedder


# --- AC-1: contract -----------------------------------------------------------


async def test_returns_k_populated_chunks_in_non_increasing_score_order(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    retriever, _ = _retriever(session_factory)
    results = await retriever.retrieve(PROBE_QUERY, k=8)

    assert len(results) == 8
    scores = [chunk.score for chunk in results]
    assert scores == sorted(scores, reverse=True)
    for chunk in results:
        assert chunk.section_path
        assert chunk.document_title
        assert chunk.source_uri.startswith("synthetic://")
        assert chunk.doc_type in {"regulation", "filing", "standard"}
        assert chunk.text
        assert isinstance(chunk.chunk_id, uuid.UUID)
        assert chunk.vector_rank is not None


async def test_ordering_is_deterministic_across_calls(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    retriever, _ = _retriever(session_factory)
    first = await retriever.retrieve(PROBE_QUERY, k=8)
    second = await retriever.retrieve(PROBE_QUERY, k=8)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]


async def test_k_is_honored(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    retriever, _ = _retriever(session_factory)
    assert len(await retriever.retrieve(PROBE_QUERY, k=3)) == 3
    assert len(await retriever.retrieve(PROBE_QUERY, k=20)) == 20


# --- AC-3: hybrid mechanics ---------------------------------------------------


# --- AC-4: embedder identity --------------------------------------------------


async def test_mismatched_identity_raises_naming_both(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    retriever, _ = _retriever(
        session_factory, StubQueryEmbedder(identity="openai:text-embedding-3-small")
    )
    with pytest.raises(EmbedderMismatchError) as excinfo:
        await retriever.retrieve(PROBE_QUERY)

    message = str(excinfo.value)
    assert "openai:text-embedding-3-small" in message
    assert "fake:test-v1" in message


async def test_matching_identity_returns_results(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    retriever, _ = _retriever(session_factory, StubQueryEmbedder(identity="fake:test-v1"))
    assert await retriever.retrieve(PROBE_QUERY, k=4)


async def test_mixed_corpus_identities_raise(
    pooled_engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    corpus = await seed_corpus(
        pooled_engine,
        {"mixed": ("Mixed", "synthetic://mixed", "regulation")},
        [SeededChunk("mixed", "First mixed passage about oversight.", "Mixed › S1")],
        identity="openai:text-embedding-3-small",
    )
    second = await seed_corpus(
        pooled_engine,
        {"mixed2": ("Mixed Two", "synthetic://mixed-2", "regulation")},
        [SeededChunk("mixed2", "Second mixed passage about oversight.", "Mixed › S2")],
        identity="fake:sha256-v1",
    )
    try:
        retriever, _ = _retriever(
            session_factory, StubQueryEmbedder(identity="openai:text-embedding-3-small")
        )
        with pytest.raises(EmbedderMismatchError):
            await retriever.retrieve("oversight")
    finally:
        await drop_documents(pooled_engine, corpus.document_ids.values())
        await drop_documents(pooled_engine, second.document_ids.values())


async def test_empty_corpus_raises(
    pooled_engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Runs against an empty scratch database — the shared test DB is seeded."""
    from sqlalchemy.ext.asyncio import create_async_engine

    scratch = "rag_retrieval_empty_test"
    await _recreate_database(scratch)
    scratch_url = DATABASE_URL.rsplit("/", 1)[0] + f"/{scratch}"
    await asyncio.to_thread(_alembic, scratch_url, "upgrade", "head")

    engine = create_async_engine(scratch_url, pool_size=2, max_overflow=0)
    try:
        retriever, _ = _retriever(async_sessionmaker(engine, expire_on_commit=False))
        with pytest.raises(EmptyCorpusError):
            await retriever.retrieve(PROBE_QUERY)
    finally:
        await engine.dispose()


# --- AC-7: concurrency (SPEC-002 Key decision 5) ------------------------------


# --- AC-8 (CI tier): latency floor on a fake embedder -------------------------


async def test_p95_latency_ci_bound(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    """Deliberately generous (SPEC-004 KD-12): catches structural regressions,
    not CI's shared-I/O noise. The real p95 target lives in the local tier."""
    assert seeded_corpus.total_chunks >= 200

    retriever, _ = _retriever(session_factory)
    await retriever.retrieve(PROBE_QUERY, k=8)  # warm the pool/index

    samples: list[float] = []
    for _ in range(50):
        started = time.perf_counter()
        await retriever.retrieve(PROBE_QUERY, k=8)
        samples.append((time.perf_counter() - started) * 1000)

    p95 = statistics.quantiles(samples, n=20)[-1]
    assert p95 <= 500, f"p95 {p95:.0f}ms over 50 calls (median {statistics.median(samples):.0f}ms)"


# --- AC-9: per-query instrumentation ------------------------------------------


async def test_query_log_record_carries_diversity_and_stage_latencies(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_corpus: SeededCorpus,
    caplog: pytest.LogCaptureFixture,
) -> None:
    retriever, _ = _retriever(session_factory)
    with caplog.at_level(logging.INFO, logger="rag_qa.retrieval.service"):
        results = await retriever.retrieve(PROBE_QUERY, k=8)

    records = [r for r in caplog.records if r.name == "rag_qa.retrieval.service"]
    assert len(records) == 1
    record = records[0]

    from rag_qa.retrieval.metrics import distinct_section_rate

    assert record.distinct_section_rate == distinct_section_rate(results)  # type: ignore[attr-defined]
    for field in ("embed_ms", "vector_ms", "total_ms"):
        assert isinstance(getattr(record, field), float)
    assert record.result_count == 8  # type: ignore[attr-defined]
    assert record.k == 8  # type: ignore[attr-defined]
    # The raw query never reaches the log; only a short digest.
    assert PROBE_QUERY not in record.getMessage()
    assert len(record.query_sha) == 12  # type: ignore[attr-defined]


def test_diversity_metric_importable_without_a_database() -> None:
    """SPEC-007 imports this same function for eval aggregation, so it must not
    drag in a database connection."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from rag_qa.retrieval.metrics import distinct_section_rate;"
            "print(distinct_section_rate([]))",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.0"


# --- AC-10: filtered retrieval still returns k --------------------------------


async def test_filtered_query_still_returns_k(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    """The corpus-wide dense AND lexical winners both live outside the filing,
    so post-filtering would return fewer than k (or nothing)."""
    filing_id = seeded_corpus.document_ids["filing"]
    retriever, _ = _retriever(session_factory)
    results = await retriever.retrieve(
        PROBE_QUERY, k=8, filters=RetrievalFilters(document_ids=(filing_id,))
    )

    assert len(results) == 8
    assert {chunk.document_id for chunk in results} == {filing_id}


async def test_filter_by_doc_type(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    retriever, _ = _retriever(session_factory)
    results = await retriever.retrieve(
        PROBE_QUERY, k=8, filters=RetrievalFilters(doc_types=("filing",))
    )
    assert results
    assert {chunk.doc_type for chunk in results} == {"filing"}


# --- AC-12: degenerate inputs -------------------------------------------------


async def test_empty_and_whitespace_queries_raise_before_any_io(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    retriever, embedder = _retriever(session_factory)

    for query in ("", "   \n\t "):
        with pytest.raises(ValueError):
            await retriever.retrieve(query)

    assert embedder.calls == []  # no embedding round-trip for an empty query


async def test_fewer_candidates_than_k_returns_what_exists(
    session_factory: async_sessionmaker[AsyncSession], seeded_corpus: SeededCorpus
) -> None:
    """The standard document holds 3 chunks; k=8 must return 3, not pad or raise."""
    retriever, _ = _retriever(session_factory)
    results = await retriever.retrieve(
        PROBE_QUERY, k=8, filters=RetrievalFilters(doc_types=("standard",))
    )
    assert len(results) == 3


# --- AC-5: migration 0003 -----------------------------------------------------


def _alembic(url: str, command_name: str, revision: str) -> None:
    from alembic import command as alembic_command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    getattr(alembic_command, command_name)(config, revision)


async def _recreate_database(name: str) -> None:
    from conftest import ADMIN_URL

    admin = await asyncpg.connect(ADMIN_URL)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()


async def test_migration_0003_qualifies_identities_and_backfills_doc_type() -> None:
    scratch = "rag_migration_test_0003"
    await _recreate_database(scratch)
    scratch_url = DATABASE_URL.rsplit("/", 1)[0] + f"/{scratch}"
    raw_url = scratch_url.replace("+asyncpg", "")

    await asyncio.to_thread(_alembic, scratch_url, "upgrade", "0002")

    vector_literal = "[" + ",".join(["0.1"] * 1536) + "]"
    conn = await asyncpg.connect(raw_url)
    try:
        doc_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO documents (id, source_uri, title, content_hash, byte_size) "
            "VALUES ($1, $2, $3, $4, $5)",
            doc_id,
            "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf",
            "NIST AI RMF",
            uuid.uuid4().hex + uuid.uuid4().hex,
            1024,
        )
        for ordinal, model in enumerate(("text-embedding-3-small", "fake:sha256-v1")):
            await conn.execute(
                "INSERT INTO chunks (id, document_id, ordinal, text, token_count, "
                "section_path, embedding, embedding_model) "
                f"VALUES ($1, $2, $3, $4, $5, $6, '{vector_literal}'::vector, $7)",
                uuid.uuid4(),
                doc_id,
                ordinal,
                "Chunk text about oversight.",
                5,
                "NIST AI RMF 1.0 › Core",
                model,
            )
    finally:
        await conn.close()

    await asyncio.to_thread(_alembic, scratch_url, "upgrade", "head")

    conn = await asyncpg.connect(raw_url)
    try:
        models = {
            r["embedding_model"] for r in await conn.fetch("SELECT embedding_model FROM chunks")
        }
        doc_type = await conn.fetchval("SELECT doc_type FROM documents")
        not_null = await conn.fetchval(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'documents' AND column_name = 'doc_type'"
        )
    finally:
        await conn.close()

    assert models == {"openai:text-embedding-3-small", "fake:sha256-v1"}  # qualified, idempotent
    assert doc_type == "standard"  # backfilled from the NIST source_uri
    assert not_null == "NO"

    # Target the revision, not a step count: head moved to 0004 with SPEC-005.
    await asyncio.to_thread(_alembic, scratch_url, "downgrade", "0002")

    conn = await asyncpg.connect(raw_url)
    try:
        models_after = {
            r["embedding_model"] for r in await conn.fetch("SELECT embedding_model FROM chunks")
        }
        has_doc_type = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'documents' AND column_name = 'doc_type'"
        )
    finally:
        await conn.close()

    assert models_after == {"text-embedding-3-small", "fake:sha256-v1"}
    assert has_doc_type is None


# --- AC-4(e): the ingest pipeline writes the client's identity ----------------


async def test_fake_embedder_ingest_is_distinguishable_in_the_database(
    session: AsyncSession, synth_corpus: Any
) -> None:
    """The defect SPEC-004 fixes: a --embedder fake run used to be recorded as
    'text-embedding-3-small', indistinguishable from real vectors."""
    from contextlib import asynccontextmanager

    from rag_qa.ingest.embedder import FakeLocalEmbeddingClient
    from rag_qa.ingest.pipeline import discover, ingest_paths
    from rag_qa.ingest.types import IngestConfig

    @asynccontextmanager
    async def provide():  # type: ignore[no-untyped-def]
        yield session

    config = IngestConfig()
    client = FakeLocalEmbeddingClient()
    await ingest_paths(
        discover(synth_corpus, config),
        config,
        dry_run=False,
        session_provider=provide,
        embedding_client=client,
    )

    # Scoped to this run's documents: the session-scoped retrieval corpus is
    # committed in the same database and would otherwise show up here.
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT c.embedding_model FROM chunks c "
                "JOIN documents d ON d.id = c.document_id "
                "WHERE d.source_uri NOT LIKE 'synthetic://%'"
            )
        )
    ).all()
    assert [row[0] for row in rows] == ["fake:sha256-v1"]
    assert client.identity == "fake:sha256-v1"


async def test_loaders_set_doc_type(session: AsyncSession, synth_corpus: Any) -> None:
    from contextlib import asynccontextmanager

    from rag_qa.ingest.embedder import FakeLocalEmbeddingClient
    from rag_qa.ingest.pipeline import discover, ingest_paths
    from rag_qa.ingest.types import IngestConfig

    @asynccontextmanager
    async def provide():  # type: ignore[no-untyped-def]
        yield session

    config = IngestConfig()
    await ingest_paths(
        discover(synth_corpus, config),
        config,
        dry_run=False,
        session_provider=provide,
        embedding_client=FakeLocalEmbeddingClient(),
    )

    rows = (
        await session.execute(
            text("SELECT title, doc_type FROM documents WHERE source_uri NOT LIKE 'synthetic://%'")
        )
    ).all()
    assert {row[1] for row in rows} == {"standard", "regulation", "filing"}


def test_candidate_row_is_not_a_public_export() -> None:
    """CandidateRow is a branch-internal shape; callers get RetrievedChunk."""
    import rag_qa.retrieval as retrieval

    assert "CandidateRow" not in retrieval.__all__
    assert CandidateRow.__module__ == "rag_qa.retrieval.search"

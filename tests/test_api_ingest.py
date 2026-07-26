"""POST /ingest and the per-request query_log row (SPEC-006 AC-6, AC-9)."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api_harness import ADMIN_KEY, READ_KEY, build_app, client_for, post
from rag_qa.api.routes.ingest import INGEST_LOCK_KEY, resolve_paths
from rag_qa.generation.types import Verdict
from test_ingest_embedder import FakeEmbeddingClient


class FakeVectorClient(FakeEmbeddingClient):
    """1536-dim vectors so rows satisfy the pgvector column."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        await super().embed(texts)
        return [[0.001] * 1536 for _ in texts]


# --- path resolution ----------------------------------------------------------


def test_paths_resolve_inside_the_corpus_directory_only(tmp_path: Path) -> None:
    (tmp_path / "doc.html").write_text("x", encoding="utf-8")
    assert resolve_paths(["doc.html"], tmp_path) == [(tmp_path / "doc.html").resolve()]
    assert resolve_paths(["."], tmp_path) == [tmp_path.resolve()]

    from rag_qa.api.errors import ValidationFailed

    for hostile in ("../etc/passwd", "/etc/passwd", "../../"):
        with pytest.raises(ValidationFailed):
            resolve_paths([hostile], tmp_path)


async def test_escaping_paths_are_422(tmp_path: Path) -> None:
    app = build_app(corpus_root=tmp_path)
    response = await post(app, "/ingest", {"paths": ["../"], "dry_run": True}, key=ADMIN_KEY)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# --- dry run ------------------------------------------------------------------


async def test_read_key_cannot_ingest(synth_corpus: Path) -> None:
    app = build_app(corpus_root=synth_corpus)
    assert (await post(app, "/ingest", {"dry_run": True}, key=READ_KEY)).status_code == 403


async def test_dry_run_is_the_default_and_writes_nothing(
    synth_corpus: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The destructive, billable call is the one you have to ask for."""
    embedder = FakeVectorClient()
    app = build_app(
        corpus_root=synth_corpus, session_factory=session_factory, embedding_client=embedder
    )

    async with session_factory() as session:
        before = (await session.execute(text("SELECT count(*) FROM documents"))).scalar_one()

    response = await post(app, "/ingest", {"paths": ["."]}, key=ADMIN_KEY)  # no dry_run key
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["total_chunks"] > 0
    assert body["estimated_embedding_usd"] >= 0
    assert {doc["verdict"] for doc in body["documents"]} == {"dry-run"}

    async with session_factory() as session:
        after = (await session.execute(text("SELECT count(*) FROM documents"))).scalar_one()
    assert after == before
    assert embedder.calls == []  # no embedding spend on a dry run


async def test_real_ingest_performs_the_work(
    synth_corpus: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    app = build_app(
        corpus_root=synth_corpus,
        session_factory=session_factory,
        embedding_client=FakeVectorClient(),
    )
    response = await post(app, "/ingest", {"paths": ["."], "dry_run": False}, key=ADMIN_KEY)
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is False
    assert {doc["verdict"] for doc in body["documents"]} <= {"new", "unchanged", "replace"}

    async with session_factory() as session:
        await session.execute(
            text("DELETE FROM documents WHERE source_uri NOT LIKE 'synthetic://%'")
        )
        await session.commit()


async def test_oversized_ingest_is_413_pointing_at_the_cli(
    synth_corpus: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    app = build_app(
        corpus_root=synth_corpus,
        session_factory=session_factory,
        embedding_client=FakeVectorClient(),
        ingest_max_chunks=1,
    )
    response = await post(app, "/ingest", {"paths": ["."], "dry_run": False}, key=ADMIN_KEY)
    assert response.status_code == 413
    body = response.json()["error"]
    assert body["code"] == "ingest_too_large"
    assert "rag_qa.ingest" in body["message"]


async def test_concurrent_ingest_is_409(
    synth_corpus: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Single-flight across replicas: an asyncio.Lock would serialize one replica
    while two others ingest the same documents."""
    app = build_app(
        corpus_root=synth_corpus,
        session_factory=session_factory,
        embedding_client=FakeVectorClient(),
    )
    async with session_factory() as holder:
        held = (
            await holder.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": INGEST_LOCK_KEY}
            )
        ).scalar_one()
        assert held
        try:
            response = await post(app, "/ingest", {"paths": ["."], "dry_run": False}, key=ADMIN_KEY)
        finally:
            await holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": INGEST_LOCK_KEY})
            await holder.commit()

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ingest_in_progress"


# --- AC-6: one query_log row per request --------------------------------------


async def test_query_writes_one_row_with_identity_tokens_and_cost(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from test_generation_service import CHUNKS

    question = f"API question {uuid.uuid4()}"
    app = build_app(session_factory=session_factory)
    response = await post(app, "/query", {"question": question})
    assert response.status_code == 200

    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT provider, model, verdict, answer_text, prompt_version, latency_ms, "
                    "prompt_tokens, completion_tokens, cost_usd, retrieved_chunk_ids "
                    "FROM query_log WHERE question = :q"
                ),
                {"q": question},
            )
        ).all()
        await session.execute(text("DELETE FROM query_log WHERE question = :q"), {"q": question})
        await session.commit()

    assert len(rows) == 1
    (
        provider,
        model,
        verdict,
        answer_text,
        prompt_version,
        latency,
        prompt_t,
        completion_t,
        cost,
        ids,
    ) = rows[0]
    assert (provider, model) == ("anthropic", "claude-sonnet-5")
    assert verdict == str(Verdict.ANSWERED)
    assert answer_text == response.json()["answer"]
    assert prompt_version == response.json()["usage"]["prompt_version"]
    assert (prompt_t, completion_t) == (1200, 80)
    assert cost > 0
    assert latency >= 0
    assert ids == [chunk.chunk_id for chunk in CHUNKS]


async def test_client_disconnect_still_writes_the_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The tokens were spent whether or not anyone was listening, so generation
    outlives the connection and the cost record survives."""
    import asyncio

    question = f"Disconnected {uuid.uuid4()}"
    app = build_app(session_factory=session_factory)

    async with (
        client_for(app) as http,
        http.stream("POST", "/query", json={"question": question, "stream": True}) as response,
    ):
        assert response.status_code == 200
        async for _ in response.aiter_text():
            break  # hang up after the first chunk

    for _ in range(50):  # let the background task finish
        await asyncio.sleep(0.02)
        async with session_factory() as session:
            found = (
                await session.execute(
                    text("SELECT count(*) FROM query_log WHERE question = :q"), {"q": question}
                )
            ).scalar_one()
        if found:
            break

    async with session_factory() as session:
        rows = (
            await session.execute(
                text("SELECT completion_tokens, cost_usd FROM query_log WHERE question = :q"),
                {"q": question},
            )
        ).all()
        await session.execute(text("DELETE FROM query_log WHERE question = :q"), {"q": question})
        await session.commit()

    assert len(rows) == 1
    assert rows[0][0] == 80  # real token counts, never a silent zero
    assert rows[0][1] > 0

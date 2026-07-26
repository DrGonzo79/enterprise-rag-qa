"""Tests derived from SPEC-002 acceptance criteria (written before implementation)."""

import asyncio
import uuid

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from conftest import DATABASE_URL
from rag_qa.db.models import Chunk, Document

SCRATCH_DB = "rag_migration_test"

EXPECTED_TABLES = {"documents", "chunks", "query_log", "eval_runs", "eval_results"}


def _document(**overrides: object) -> Document:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "source_uri": "https://example.com/doc.pdf",
        "title": "Test Document",
        "doc_type": "standard",  # SPEC-004 migration 0003
        "content_hash": uuid.uuid4().hex + uuid.uuid4().hex,
        "byte_size": 1024,
    }
    defaults.update(overrides)
    return Document(**defaults)  # type: ignore[arg-type]


def _chunk(document_id: uuid.UUID, **overrides: object) -> Chunk:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "document_id": document_id,
        "ordinal": 0,
        "text": "The quick brown fox discusses Article 6(2) compliance.",
        "token_count": 12,
        "section_path": "Test Doc › Chapter 1",  # SPEC-003 migration 0002
        "embedding": [0.1] * 1536,
        "embedding_model": "text-embedding-3-small",
    }
    defaults.update(overrides)
    return Chunk(**defaults)  # type: ignore[arg-type]


async def test_migrations_roundtrip() -> None:
    """AC-1: upgrade head creates all five tables; downgrade base returns to empty."""
    from alembic import command
    from alembic.config import Config

    admin_url = DATABASE_URL.replace("+asyncpg", "").rsplit("/", 1)[0] + "/rag"
    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
        await admin.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    finally:
        await admin.close()

    scratch_url = DATABASE_URL.rsplit("/", 1)[0] + f"/{SCRATCH_DB}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", scratch_url)

    async def table_names() -> set[str]:
        conn = await asyncpg.connect(scratch_url.replace("+asyncpg", ""))
        try:
            rows = await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name != 'alembic_version'"
            )
            return {r["table_name"] for r in rows}
        finally:
            await conn.close()

    # env.py calls asyncio.run, so Alembic must run outside this test's event loop
    await asyncio.to_thread(command.upgrade, config, "head")
    assert await table_names() == EXPECTED_TABLES
    await asyncio.to_thread(command.downgrade, config, "base")
    assert await table_names() == set()


async def test_indexes_exist(session: AsyncSession) -> None:
    """AC-2: HNSW index on chunks.embedding and GIN index on chunks.tsv."""
    rows = (
        await session.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'chunks'")
        )
    ).all()
    defs = {name: definition for name, definition in rows}
    assert "ix_chunks_embedding_hnsw" in defs
    assert "hnsw" in defs["ix_chunks_embedding_hnsw"].lower()
    assert "ix_chunks_tsv_gin" in defs
    assert "gin" in defs["ix_chunks_tsv_gin"].lower()


async def test_content_hash_unique(session: AsyncSession) -> None:
    """AC-3: duplicate content_hash raises an integrity error."""
    shared_hash = "a" * 64
    session.add(_document(content_hash=shared_hash))
    await session.flush()
    session.add(_document(content_hash=shared_hash))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_chunk_ordinal_unique(session: AsyncSession) -> None:
    """AC-4: duplicate (document_id, ordinal) raises an integrity error."""
    doc = _document()
    session.add(doc)
    await session.flush()
    session.add(_chunk(doc.id, ordinal=1))
    await session.flush()
    session.add(_chunk(doc.id, ordinal=1))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_embedding_dimension_enforced(session: AsyncSession) -> None:
    """AC-5: a non-1536-dimensional embedding fails."""
    doc = _document()
    session.add(doc)
    await session.flush()
    session.add(_chunk(doc.id, embedding=[0.1] * 3))
    with pytest.raises(DBAPIError):
        await session.flush()


async def test_tsv_generated_and_searchable(session: AsyncSession) -> None:
    """AC-6: tsv populates without application writes and matches to_tsquery."""
    doc = _document()
    session.add(doc)
    await session.flush()
    chunk = _chunk(doc.id, text="Reciprocal rank fusion combines retrieval results.")
    session.add(chunk)
    await session.flush()

    row = (
        await session.execute(
            text(
                "SELECT id FROM chunks "
                "WHERE tsv @@ to_tsquery('english', 'reciprocal & fusion') AND id = :id"
            ),
            {"id": chunk.id},
        )
    ).first()
    assert row is not None


async def test_document_delete_cascades(session: AsyncSession) -> None:
    """AC-7: deleting a document cascades to its chunks."""
    doc = _document()
    session.add(doc)
    await session.flush()
    session.add(_chunk(doc.id))
    await session.flush()

    await session.execute(text("DELETE FROM documents WHERE id = :id"), {"id": doc.id})
    remaining = (
        await session.execute(
            text("SELECT count(*) FROM chunks WHERE document_id = :id"), {"id": doc.id}
        )
    ).scalar_one()
    assert remaining == 0


def test_pool_bounds() -> None:
    """Key decision 8: explicit pool bounds on the production engine factory."""
    from rag_qa.db.engine import POOL_MAX_OVERFLOW, POOL_SIZE

    assert POOL_SIZE == 5
    assert POOL_MAX_OVERFLOW == 5

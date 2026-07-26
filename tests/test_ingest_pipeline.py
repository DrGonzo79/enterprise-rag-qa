"""Pipeline tests from SPEC-003 AC-6/AC-7/AC-8/AC-9: hashing, idempotent
upsert, manifest, dry-run entrypoint, migration 0002."""

import asyncio
import inspect
import json
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from conftest import DATABASE_URL
from rag_qa.ingest.pipeline import MANIFEST_FILENAME, discover, ingest_paths
from rag_qa.ingest.types import IngestConfig, compute_content_hash
from test_ingest_embedder import FakeEmbeddingClient

REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeVectorClient(FakeEmbeddingClient):
    """1536-dim vectors so rows satisfy the pgvector column."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        await super().embed(texts)
        return [[0.001] * 1536 for _ in texts]


# --- content hash (AC-6, review amendment 3) ---------------------------------


def test_content_hash_covers_every_chunk_affecting_parameter() -> None:
    raw = b"raw document bytes"
    base = IngestConfig()
    variants = [
        IngestConfig(overlap_ratio=0.10),
        IngestConfig(target_max=700),
        IngestConfig(edgar_numeric_table_threshold=0.6),
        IngestConfig(breadcrumb_format="v2:>"),
        IngestConfig(strategy="heading_v2"),
        IngestConfig(hard_min=100),
        IngestConfig(target_min=400),
    ]
    hashes = {compute_content_hash(raw, c) for c in [base, *variants]}
    assert len(hashes) == len(variants) + 1  # all distinct
    assert compute_content_hash(raw, IngestConfig()) == compute_content_hash(raw, base)
    assert compute_content_hash(b"other bytes", base) != compute_content_hash(raw, base)


# --- idempotent upsert (AC-6) -------------------------------------------------


def _provider(session: AsyncSession):
    @asynccontextmanager
    async def provide() -> AsyncIterator[AsyncSession]:
        yield session

    return provide


async def _counts(session: AsyncSession) -> tuple[int, int]:
    docs = (await session.execute(text("SELECT count(*) FROM documents"))).scalar_one()
    chunks = (await session.execute(text("SELECT count(*) FROM chunks"))).scalar_one()
    return docs, chunks


async def test_second_run_skips_everything(session: AsyncSession, synth_corpus: Path) -> None:
    config = IngestConfig()
    paths = discover(synth_corpus, config)
    assert len(paths) == 3
    client = FakeVectorClient()

    first = await ingest_paths(
        paths, config, dry_run=False, session_provider=_provider(session), embedding_client=client
    )
    assert {r.verdict for r in first.documents} == {"new"}
    docs_after_first, chunks_after_first = await _counts(session)
    assert docs_after_first == 3
    assert chunks_after_first > 0
    calls_after_first = len(client.calls)
    assert calls_after_first > 0

    second = await ingest_paths(
        paths, config, dry_run=False, session_provider=_provider(session), embedding_client=client
    )
    assert {r.verdict for r in second.documents} == {"skip"}
    assert await _counts(session) == (docs_after_first, chunks_after_first)
    assert len(client.calls) == calls_after_first  # zero embedding calls on run 2


async def test_config_change_replaces_chunks(session: AsyncSession, synth_corpus: Path) -> None:
    config_a = IngestConfig()
    config_b = IngestConfig(overlap_ratio=0.10)
    paths = discover(synth_corpus, config_a)
    client = FakeVectorClient()

    await ingest_paths(
        paths, config_a, dry_run=False, session_provider=_provider(session), embedding_client=client
    )
    docs_a, _ = await _counts(session)
    ids_a = {row[0] for row in await session.execute(text("SELECT id FROM chunks"))}
    hashes_a = {row[0] for row in await session.execute(text("SELECT content_hash FROM documents"))}

    result = await ingest_paths(
        paths, config_b, dry_run=False, session_provider=_provider(session), embedding_client=client
    )
    assert {r.verdict for r in result.documents} == {"replace"}
    docs_b, _ = await _counts(session)
    ids_b = {row[0] for row in await session.execute(text("SELECT id FROM chunks"))}
    hashes_b = {row[0] for row in await session.execute(text("SELECT content_hash FROM documents"))}

    assert docs_b == docs_a  # document count unchanged
    assert ids_a.isdisjoint(ids_b)  # all chunk ids replaced
    assert hashes_a.isdisjoint(hashes_b)  # content_hash differs per document


async def test_chunks_carry_section_path_and_model(
    session: AsyncSession, synth_corpus: Path
) -> None:
    config = IngestConfig()
    await ingest_paths(
        discover(synth_corpus, config),
        config,
        dry_run=False,
        session_provider=_provider(session),
        embedding_client=FakeVectorClient(),
    )
    rows = (
        await session.execute(text("SELECT section_path, embedding_model, ordinal FROM chunks"))
    ).all()
    assert rows
    assert all(path and "›" in path for path, _, _ in rows)
    assert all(model == "text-embedding-3-small" for _, model, _ in rows)


# --- manifest (AC-9) ----------------------------------------------------------


async def test_dry_run_writes_manifest(synth_corpus: Path) -> None:
    config = IngestConfig()
    manifest = await ingest_paths(
        discover(synth_corpus, config), config, dry_run=True, manifest_dir=synth_corpus
    )
    assert {r.verdict for r in manifest.documents} == {"dry-run"}

    payload = json.loads((synth_corpus / MANIFEST_FILENAME).read_text())
    assert payload["config"]["strategy"] == "heading_v1"
    assert len(payload["documents"]) == 3

    dropped = payload["dropped_tables"]
    assert len(dropped) == 1  # the synthetic financial table, nothing else
    record = dropped[0]
    assert record["document"] == "synth-10k.htm"
    assert record["item"] == "Item 1A. Risk Factors"
    assert record["reason"] == "numeric_table_threshold"
    assert record["digit_ratio"] >= 0.5
    assert record["cell_count"] == 9
    assert isinstance(record["table_index"], int)


def test_manifest_is_gitignored() -> None:
    result = subprocess.run(
        ["git", "check-ignore", f"corpus/{MANIFEST_FILENAME}"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    assert result.returncode == 0


# --- entrypoint & dry-run CLI (AC-8) -------------------------------------------


def test_cli_dry_run_needs_no_db_or_api_key(synth_corpus: Path) -> None:
    import os

    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "OPENAI_API_KEY")}
    result = subprocess.run(
        [sys.executable, "-m", "rag_qa.ingest", str(synth_corpus), "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "chunks" in result.stdout
    assert "dry-run" in result.stdout


def test_entrypoint_is_sync_over_asyncio_run() -> None:
    from rag_qa.ingest.__main__ import cli

    assert not asyncio.iscoroutinefunction(cli)
    assert "asyncio.run(" in inspect.getsource(cli)


# --- migration 0002 (AC-7) ------------------------------------------------------


async def test_migration_0002_roundtrip() -> None:
    from alembic import command
    from alembic.config import Config

    scratch = "rag_migration_test_0002"
    admin_url = DATABASE_URL.replace("+asyncpg", "").rsplit("/", 1)[0] + "/rag"
    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
        await admin.execute(f'CREATE DATABASE "{scratch}"')
    finally:
        await admin.close()

    scratch_url = DATABASE_URL.rsplit("/", 1)[0] + f"/{scratch}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", scratch_url)

    async def has_section_path() -> bool:
        conn = await asyncpg.connect(scratch_url.replace("+asyncpg", ""))
        try:
            row = await conn.fetchrow(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'chunks' AND column_name = 'section_path'"
            )
            return row is not None
        finally:
            await conn.close()

    await asyncio.to_thread(command.upgrade, config, "head")
    assert await has_section_path()
    await asyncio.to_thread(command.downgrade, config, "-1")
    assert not await has_section_path()

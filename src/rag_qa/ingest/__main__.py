"""`python -m rag_qa.ingest` — sync entrypoint over async internals
(SPEC-002 Key decision 5)."""

import argparse
import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from rag_qa.env import load_env
from rag_qa.ingest.pipeline import Manifest, discover, ingest_paths
from rag_qa.ingest.types import IngestConfig


async def _amain(directory: Path, dry_run: bool, embedder: str) -> Manifest:
    config = IngestConfig()
    paths = discover(directory, config)
    if not paths:
        raise SystemExit(f"no ingestible documents found in {directory}")

    if dry_run:
        return await ingest_paths(paths, config, dry_run=True, manifest_dir=directory)

    # DB and API clients are constructed only outside --dry-run (AC-8).
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

    from rag_qa.db.engine import create_engine, create_session_factory
    from rag_qa.ingest.embedder import FakeLocalEmbeddingClient, OpenAIEmbeddingClient

    engine = create_engine()
    factory = create_session_factory(engine)

    @asynccontextmanager
    async def provide_session() -> AsyncGenerator[AsyncSession]:
        async with factory() as session:
            yield session

    client = FakeLocalEmbeddingClient() if embedder == "fake" else OpenAIEmbeddingClient()
    try:
        return await ingest_paths(
            paths,
            config,
            dry_run=False,
            session_provider=provide_session,
            embedding_client=client,
            manifest_dir=directory,
        )
    finally:
        await engine.dispose()


def cli(argv: list[str] | None = None) -> int:
    load_env()  # .env fills gaps for local runs; real env vars win (SPEC-001 KD-6)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m rag_qa.ingest")
    parser.add_argument("directory", type=Path, help="corpus directory")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and chunk only: report counts and cost, no DB, no API calls",
    )
    parser.add_argument(
        "--embedder",
        choices=("openai", "fake"),
        default="openai",
        help="'fake' = deterministic offline vectors (smoke runs, idempotency tests)",
    )
    args = parser.parse_args(argv)

    manifest = asyncio.run(_amain(args.directory, args.dry_run, args.embedder))

    for report in manifest.documents:
        print(
            f"{report.document}: {report.verdict} — {report.sections} sections, "
            f"{report.chunks} chunks, {report.tokens} tokens, "
            f"est. ${report.estimated_embedding_usd:.4f}"
        )
    if manifest.dropped_tables:
        print(f"dropped tables: {len(manifest.dropped_tables)} (see ingest-manifest.json)")
    return 0


if __name__ == "__main__":
    sys.exit(cli())

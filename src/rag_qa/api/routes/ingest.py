"""POST /ingest — synchronous, admin-scoped, dry-run by default (SPEC-006 KD-12).

Bounded three ways, each from a named failure: a **Postgres advisory lock** makes
ingestion single-flight *across replicas* (an asyncio.Lock would serialize one
replica while two others ingest the same documents — SPEC-002 KD-8 sizes for
three); paths resolve inside the corpus directory only; and a real ingest whose
dry-run manifest exceeds the chunk bound is refused, pointing at the CLI.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_qa.api.auth import Scope, require
from rag_qa.api.context import current_request_id
from rag_qa.api.deps import AppState
from rag_qa.api.errors import (
    IngestInProgress,
    IngestTooLarge,
    Misconfigured,
    ValidationFailed,
)
from rag_qa.api.schemas import ErrorResponse, IngestRequest, IngestResponse
from rag_qa.ingest.pipeline import discover, ingest_paths
from rag_qa.ingest.types import IngestConfig

router = APIRouter()

# Arbitrary but fixed: one lock id for "corpus ingestion" across the deployment.
INGEST_LOCK_KEY = 0x5241_4751  # "RAGQ"


def resolve_paths(requested: list[str], corpus_root: Path) -> list[Path]:
    """Resolve inside the corpus directory only — no arbitrary server paths."""
    root = corpus_root.resolve()
    resolved: list[Path] = []
    for raw in requested:
        candidate = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValidationFailed(f"path {raw!r} resolves outside the corpus directory")
        resolved.append(candidate)
    return resolved


@asynccontextmanager
async def single_flight(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[None]:
    async with session_factory() as session:
        held = (
            await session.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": INGEST_LOCK_KEY}
            )
        ).scalar_one()
        if not held:
            raise IngestInProgress("another ingest is running; retry when it finishes")
        try:
            yield
        finally:
            await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": INGEST_LOCK_KEY})
            await session.commit()


@router.post(
    "/ingest",
    response_model=IngestResponse,
    summary="Ingest server-side corpus documents (admin key required)",
    dependencies=[Depends(require(Scope.ADMIN))],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def ingest(request: Request, payload: IngestRequest) -> IngestResponse:
    state: AppState = request.app.state.rag
    settings = state.settings
    paths = resolve_paths(payload.paths, settings.corpus_root)

    config = IngestConfig()
    files: list[Path] = []
    for path in paths:
        files.extend(discover(path, config) if path.is_dir() else [path])
    if not files:
        raise ValidationFailed("no ingestable documents found at the requested paths")

    # Always price the work first: the bound is checked against a measurement,
    # and the dry-run path needs neither a database nor an embedding key.
    manifest = await ingest_paths(files, config, dry_run=True)
    total_chunks = sum(report.chunks for report in manifest.documents)

    if payload.dry_run:
        return IngestResponse.build(manifest, dry_run=True, request_id=current_request_id())

    if total_chunks > settings.ingest_max_chunks:
        raise IngestTooLarge(
            f"{total_chunks} chunks exceeds the {settings.ingest_max_chunks}-chunk bound for a "
            "synchronous ingest; run `python -m rag_qa.ingest <path>` from the CLI instead"
        )
    if state.session_factory is None or state.embedding_client is None:
        raise Misconfigured("ingestion requires a database and an embedding client")

    async with single_flight(state.session_factory):
        performed = await ingest_paths(
            files,
            config,
            dry_run=False,
            session_provider=state.session_factory,
            embedding_client=state.embedding_client,
        )
    return IngestResponse.build(performed, dry_run=False, request_id=current_request_id())

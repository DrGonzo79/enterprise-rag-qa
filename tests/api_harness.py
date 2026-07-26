"""Shared harness for the SPEC-006 API tests.

The savepoint fixtures from SPEC-002 cannot host these tests and the reason must
not be rediscovered: the app opens **its own** sessions from **its own** factory,
and SPEC-004 needs two connections concurrently, so an API test on the savepoint
fixture fails two ways at once — the app cannot see the fixture's uncommitted
rows, and the app's own writes commit outside the rollback and leak. Tests that
need real rows use SPEC-004's pattern instead (commit, delete by id).
"""

from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_qa.api import create_app
from rag_qa.api.deps import Settings
from rag_qa.generation.service import Generator
from rag_qa.retrieval.types import RetrievalFilters, RetrievedChunk
from test_generation_service import CHUNKS, FakeLLMClient

READ_KEY = "test-read-key"
ADMIN_KEY = "test-admin-key"


def settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"api_key": READ_KEY, "admin_api_key": ADMIN_KEY}
    base.update(overrides)
    return Settings(**base)


class StubRetriever:
    """Records its calls; raises on demand so error mapping is exercisable."""

    def __init__(
        self,
        chunks: Sequence[RetrievedChunk] | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self._chunks = list(chunks if chunks is not None else CHUNKS)
        self._error = error
        self._delay = delay
        self.calls: list[tuple[str, int, RetrievalFilters | None]] = []

    async def retrieve(
        self, query: str, k: int = 8, filters: RetrievalFilters | None = None
    ) -> list[RetrievedChunk]:
        import asyncio

        self.calls.append((query, k, filters))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._chunks


def build_app(
    retriever: Any = None,
    client: FakeLLMClient | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    embedding_client: object | None = None,
    **setting_overrides: Any,
) -> FastAPI:
    return create_app(
        settings=settings(**setting_overrides),
        retriever=retriever if retriever is not None else StubRetriever(),  # type: ignore[arg-type]
        generator=Generator(client or FakeLLMClient(), session_factory=session_factory),
        session_factory=session_factory,
        embedding_client=embedding_client,
    )


def client_for(app: FastAPI, key: str | None = READ_KEY) -> httpx.AsyncClient:
    headers = {"X-API-Key": key} if key is not None else {}
    # raise_app_exceptions=False mirrors a real ASGI server: Starlette's
    # ServerErrorMiddleware sends the 500 envelope and then re-raises so the
    # server can log it. Without this the transport would re-raise instead of
    # returning the response a caller actually receives.
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        headers=headers,
    )


async def post(app: FastAPI, path: str, payload: Any, key: str | None = READ_KEY) -> httpx.Response:
    async with client_for(app, key) as http:
        return await http.post(path, json=payload)


async def get(app: FastAPI, path: str, key: str | None = READ_KEY) -> httpx.Response:
    async with client_for(app, key) as http:
        return await http.get(path)


async def sse_frames(
    app: FastAPI, payload: Any, key: str | None = READ_KEY
) -> tuple[httpx.Response, list[str]]:
    """Raw frames, split by the SSE record separator.

    Deliberately not an SSE client library: the framing *is* what is under test,
    and a library that tolerates a malformed frame would hide the bug.
    """
    async with (
        client_for(app, key) as http,
        http.stream("POST", "/query", json=payload) as response,
    ):
        body = "".join([chunk async for chunk in response.aiter_text()])
        return response, [frame for frame in body.split("\n\n") if frame]


def data_payloads(frames: Sequence[str]) -> list[dict[str, Any]]:
    import json

    return [
        json.loads(frame.removeprefix("data: ")) for frame in frames if frame.startswith("data: ")
    ]


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with app.router.lifespan_context(app):
        yield

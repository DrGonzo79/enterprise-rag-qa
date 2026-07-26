"""Application factory (SPEC-006 KD-11).

`create_app()` builds the app object and performs no I/O; the engine is created
in `lifespan`, on the running loop, and disposed on shutdown. The previous
import-time wiring mounted `/ask` **only if** `DATABASE_URL` and
`ANTHROPIC_API_KEY` happened to be set, so a misconfigured deployment answered
**404** — which reads as "wrong URL" and sends an operator to look at routing
rather than at configuration. Startup now fails with a named cause instead.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from rag_qa.api.budget import SpendGuard
from rag_qa.api.context import current_request_id, install_log_record_factory
from rag_qa.api.deps import AppState, Settings
from rag_qa.api.errors import ApiError, envelope
from rag_qa.api.metrics import Metrics
from rag_qa.api.middleware import MetricsMiddleware, RequestContextMiddleware
from rag_qa.api.routes import health, ingest, metrics, query
from rag_qa.api.schemas import ErrorResponse
from rag_qa.generation.service import Generator
from rag_qa.observability import configure_logging
from rag_qa.retrieval.service import Retriever

logger = logging.getLogger(__name__)

TITLE = "enterprise-rag-qa"
DESCRIPTION = (
    "Retrieval-augmented Q&A over public compliance documents. "
    "HTTP status describes the transport; `verdict` describes the outcome — a "
    "refusal is a successful request (SPEC-006 Key decision 1)."
)


def create_app(
    *,
    settings: Settings | None = None,
    retriever: Retriever | None = None,
    generator: Generator | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    embedding_client: object | None = None,
) -> FastAPI:
    resolved = settings if settings is not None else Settings.from_env()
    # The factory stamps request_id onto every record; configure_logging is what
    # makes any of it reach an operator. Shipping the first without the second
    # was the defect (KD-5, amended).
    install_log_record_factory()
    configure_logging()

    state = AppState(
        settings=resolved,
        metrics=Metrics(),
        budget=SpendGuard(
            session_factory,
            daily_limit_usd=resolved.daily_budget_usd,
            monthly_limit_usd=resolved.monthly_budget_usd,
            refresh_seconds=resolved.budget_refresh_seconds,
        ),
        query_semaphore=asyncio.Semaphore(resolved.max_concurrent_queries),
        retriever=retriever,
        generator=generator,
        session_factory=session_factory,
        embedding_client=embedding_client,  # type: ignore[arg-type]
    )

    app = FastAPI(title=TITLE, description=DESCRIPTION, lifespan=_lifespan)
    app.state.settings = resolved
    app.state.rag = state

    app.add_middleware(MetricsMiddleware, metrics=state.metrics)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)
    app.include_router(query.router)
    app.include_router(ingest.router)
    app.include_router(metrics.router)

    _install_error_handlers(app)
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    state: AppState = app.state.rag
    settings = state.settings
    # Only require what must actually be built: an app handed a retriever and a
    # generator needs no provider configuration.
    needs_providers = state.retriever is None or state.generator is None
    settings.require_serving(needs_providers=needs_providers)

    if needs_providers:
        _build_dependencies(state)

    logger.info(
        "api starting",
        extra={
            "max_concurrent_queries": settings.max_concurrent_queries,
            "budget_enabled": state.budget.enabled,
            "anonymous": settings.allow_anonymous,
        },
    )
    try:
        yield
    finally:
        if state.engine is not None:
            await state.engine.dispose()


def _build_dependencies(state: AppState) -> None:
    from rag_qa.db.engine import create_engine, create_session_factory
    from rag_qa.generation.clients.anthropic import AnthropicClient
    from rag_qa.ingest.embedder import OpenAIEmbeddingClient

    assert state.settings.database_url is not None
    engine = create_engine(state.settings.database_url)
    factory = create_session_factory(engine)
    embedder = OpenAIEmbeddingClient()

    state.engine = engine
    state.session_factory = factory
    state.embedding_client = embedder
    state.retriever = Retriever(factory, embedder)
    state.generator = Generator(AnthropicClient(), session_factory=factory)
    state.budget = SpendGuard(
        factory,
        daily_limit_usd=state.settings.daily_budget_usd,
        monthly_limit_usd=state.settings.monthly_budget_usd,
        refresh_seconds=state.settings.budget_refresh_seconds,
    )


def _envelope(error: ApiError) -> JSONResponse:
    status, body, headers = envelope(error, current_request_id())
    # Validated through the declared model so the wire shape and the OpenAPI
    # schema cannot drift apart.
    return JSONResponse(
        ErrorResponse.model_validate(body).model_dump(), status_code=status, headers=headers
    )


async def _handle_api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return _envelope(exc)


async def _handle_validation(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    error = ApiError(_summarize_validation(exc))
    error.status_code, error.code = 422, "validation_error"
    return _envelope(error)


def _summarize_validation(exc: RequestValidationError) -> str:
    parts = [
        f"{'.'.join(str(p) for p in item.get('loc', ())[1:])}: {item.get('msg', '')}".strip(": ")
        for item in exc.errors()
    ]
    return "; ".join(part for part in parts if part) or "invalid request body"


async def _handle_http(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    error = ApiError(str(exc.detail))
    error.status_code = exc.status_code
    error.code = "not_found" if exc.status_code == 404 else "http_error"
    return _envelope(error)


def _install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_validation)
    app.add_exception_handler(StarletteHTTPException, _handle_http)
    # No handler for bare Exception: Starlette routes that one to
    # ServerErrorMiddleware, which sits outside RequestContextMiddleware and
    # would answer without the request-id header. RequestContextMiddleware
    # finishes those instead.

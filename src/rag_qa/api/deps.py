"""Settings and per-app state (SPEC-006 KD-11).

`Settings.from_env()` never raises: importing `rag_qa.main` must not depend on a
configured environment, or the whole test suite needs a database to import an app
that serves `/healthz`. Validation that *does* raise runs in `lifespan`, so a
misconfigured deployment fails at startup with a named cause instead of serving
404s on an endpoint that quietly was not mounted.
"""

import asyncio
import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from rag_qa.api.budget import SpendGuard
from rag_qa.api.concurrency import MAX_CONCURRENT_QUERIES
from rag_qa.api.metrics import Metrics
from rag_qa.generation.service import Generator
from rag_qa.ingest.embedder import EmbeddingClient
from rag_qa.retrieval.service import Retriever

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CORPUS_ROOT = REPO_ROOT / "corpus"


class ConfigurationError(RuntimeError):
    """Raised at startup, naming the variable that is missing."""


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


def _decimal(name: str) -> Decimal | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ConfigurationError(f"{name}={raw!r} is not a decimal amount") from exc


@dataclass(frozen=True)
class Settings:
    database_url: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    api_key: str | None = None
    admin_api_key: str | None = None
    allow_anonymous: bool = False
    max_concurrent_queries: int = MAX_CONCURRENT_QUERIES
    query_acquire_timeout_seconds: float = 2.0
    sse_heartbeat_seconds: float = 15.0
    ingest_max_chunks: int = 5000
    corpus_root: Path = field(default=DEFAULT_CORPUS_ROOT)
    daily_budget_usd: Decimal | None = None
    budget_refresh_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("DATABASE_URL"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            api_key=os.environ.get("RAG_QA_API_KEY"),
            admin_api_key=os.environ.get("RAG_QA_ADMIN_API_KEY"),
            allow_anonymous=bool(os.environ.get("RAG_QA_ALLOW_ANONYMOUS")),
            max_concurrent_queries=_int("RAG_QA_MAX_CONCURRENT_QUERIES", MAX_CONCURRENT_QUERIES),
            sse_heartbeat_seconds=_float("RAG_QA_SSE_HEARTBEAT_SECONDS", 15.0),
            ingest_max_chunks=_int("INGEST_MAX_CHUNKS", 5000),
            daily_budget_usd=_decimal("RAG_QA_DAILY_BUDGET_USD"),
            budget_refresh_seconds=_float("RAG_QA_BUDGET_REFRESH_SECONDS", 30.0),
        )

    def require_serving(self, *, needs_providers: bool) -> None:
        """Fail loudly at startup rather than 404 on an unmounted route.

        A missing key previously left `/ask` unmounted, so a misconfigured
        deployment answered 404 — which reads as "wrong URL" and sends an
        operator to look at routing instead of configuration.
        """
        missing: list[str] = []
        if needs_providers:
            if not self.database_url:
                missing.append("DATABASE_URL")
            if not self.anthropic_api_key:
                missing.append("ANTHROPIC_API_KEY")
            if not self.openai_api_key:
                missing.append("OPENAI_API_KEY (query embeddings)")
        if missing:
            raise ConfigurationError(
                "cannot serve without: " + ", ".join(missing) + ". "
                "Set them in the environment or in .env for local runs."
            )
        # A service whose auth silently disables itself when a variable is unset
        # is worse than one with no auth, because it looks protected.
        if not self.allow_anonymous and not (self.api_key or self.admin_api_key):
            raise ConfigurationError(
                "no API key configured: set RAG_QA_API_KEY (and optionally "
                "RAG_QA_ADMIN_API_KEY), or set RAG_QA_ALLOW_ANONYMOUS=1 to serve "
                "without authentication deliberately"
            )


@dataclass
class AppState:
    settings: Settings
    metrics: Metrics
    budget: SpendGuard
    query_semaphore: asyncio.Semaphore
    retriever: Retriever | None = None
    generator: Generator | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    engine: AsyncEngine | None = None
    embedding_client: EmbeddingClient | None = None

"""Batched embedding with bounded concurrency (SPEC-003 Interface).

Retry lives in embed_all (not the client) so tests can exercise it with a
fake client raising transient errors.
"""

import asyncio
import logging
from typing import Protocol

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
BATCH_SIZE = 128
CONCURRENCY = 4
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0


class EmbeddingClient(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class RetryableEmbeddingError(Exception):
    """Raised by clients for transient failures (429, 5xx, connection)."""


class OpenAIEmbeddingClient:
    """Thin adapter over the OpenAI SDK; transient errors are re-raised as
    RetryableEmbeddingError so embed_all owns the retry policy."""

    def __init__(self, model: str = EMBEDDING_MODEL) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI()
        self._model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import openai

        try:
            response = await self._client.embeddings.create(model=self._model, input=texts)
        except (openai.RateLimitError, openai.APIConnectionError) as exc:
            raise RetryableEmbeddingError(str(exc)) from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise RetryableEmbeddingError(str(exc)) from exc
            raise
        return [item.embedding for item in response.data]


class FakeLocalEmbeddingClient:
    """Offline embedder for smoke runs and cross-process idempotency tests
    (`--embedder fake`): deterministic pseudo-vectors derived from the text's
    sha256, no network. When RAG_QA_FAKE_EMBEDDER_LOG names a file, each
    embed() call appends one line — tests count lines to prove the second
    ingest of an unchanged corpus makes zero embedding calls."""

    def __init__(self, dim: int = 1536) -> None:
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib
        import os

        log_path = os.environ.get("RAG_QA_FAKE_EMBEDDER_LOG")
        if log_path:
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(f"embed {len(texts)}\n")
        vectors: list[list[float]] = []
        for text in texts:
            seed = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append([seed[i % len(seed)] / 255.0 for i in range(self._dim)])
        return vectors


async def embed_all(
    texts: list[str],
    client: EmbeddingClient,
    *,
    batch_size: int = BATCH_SIZE,
    concurrency: int = CONCURRENCY,
    max_attempts: int = MAX_ATTEMPTS,
    backoff_base_seconds: float = BACKOFF_BASE_SECONDS,
) -> list[list[float]]:
    """Embed texts in order-preserving batches under a concurrency bound."""
    batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]
    semaphore = asyncio.Semaphore(concurrency)

    async def run_batch(batch: list[str]) -> list[list[float]]:
        async with semaphore:
            for attempt in range(1, max_attempts + 1):
                try:
                    return await client.embed(batch)
                except RetryableEmbeddingError:
                    if attempt == max_attempts:
                        raise
                    delay = backoff_base_seconds * 2 ** (attempt - 1)
                    logger.warning(
                        "retryable embedding error (attempt %d/%d), backing off %.1fs",
                        attempt,
                        max_attempts,
                        delay,
                    )
                    await asyncio.sleep(delay)
            raise AssertionError("unreachable")

    results = await asyncio.gather(*(run_batch(b) for b in batches))
    return [vector for batch in results for vector in batch]

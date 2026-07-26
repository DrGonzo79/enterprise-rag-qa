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

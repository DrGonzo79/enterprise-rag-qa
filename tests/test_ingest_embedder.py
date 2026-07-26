"""Embedder tests from SPEC-003 AC-5: batching, bounded concurrency, retry."""

import asyncio

import pytest

from rag_qa.ingest.embedder import RetryableEmbeddingError, embed_all


class FakeEmbeddingClient:
    """Deterministic vectors + instrumentation for AC-5/AC-6 assertions."""

    identity = "fake:test-v1"  # EmbeddingClient protocol (SPEC-004)

    def __init__(self, fail_first_n: int = 0) -> None:
        self.calls: list[int] = []  # batch sizes, in completion order
        self.in_flight = 0
        self.max_in_flight = 0
        self._remaining_failures = fail_first_n

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise RetryableEmbeddingError("simulated 429")
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.005)
        self.in_flight -= 1
        self.calls.append(len(texts))
        return [[float(len(t))] * 3 for t in texts]


async def test_batching_and_order() -> None:
    texts = [f"text-{i}" * (i % 7 + 1) for i in range(300)]
    client = FakeEmbeddingClient()
    vectors = await embed_all(texts, client, batch_size=128, concurrency=4)

    assert len(vectors) == 300
    assert sum(client.calls) == 300
    assert all(size <= 128 for size in client.calls)
    assert len(client.calls) == 3  # ceil(300/128)
    # Order preserved: vector encodes its text's length.
    assert all(vec == [float(len(t))] * 3 for t, vec in zip(texts, vectors, strict=True))


async def test_concurrency_bound() -> None:
    texts = [f"t{i}" for i in range(200)]
    client = FakeEmbeddingClient()
    await embed_all(texts, client, batch_size=10, concurrency=4)
    assert client.max_in_flight <= 4
    assert len(client.calls) == 20


async def test_simulated_429_retried() -> None:
    texts = ["a", "b", "c"]
    client = FakeEmbeddingClient(fail_first_n=1)
    vectors = await embed_all(texts, client, batch_size=128, backoff_base_seconds=0.001)
    assert len(vectors) == 3


async def test_retries_exhausted_raises() -> None:
    client = FakeEmbeddingClient(fail_first_n=99)
    with pytest.raises(RetryableEmbeddingError):
        await embed_all(["x"], client, max_attempts=3, backoff_base_seconds=0.001)

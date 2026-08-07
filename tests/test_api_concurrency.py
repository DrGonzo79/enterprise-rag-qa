"""Concurrency bound and the deadlock it prevents (SPEC-006 AC-8, KD-10)."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from api_harness import READ_KEY, StubRetriever, build_app, client_for
from conftest import DATABASE_URL, PROBE_QUERY, SeededCorpus, StubQueryEmbedder
from rag_qa.api.concurrency import (
    CONNECTIONS_PER_QUERY,
    DERIVED_MAX_CONCURRENT_QUERIES,
    MAX_CONCURRENT_QUERIES,
    QUERY_CONNECTIONS,
    RESERVED,
    RESERVED_CONNECTIONS,
    max_concurrent_queries,
)
from rag_qa.db.engine import POOL_MAX_OVERFLOW, POOL_SIZE
from rag_qa.retrieval import Retriever

QUESTION = {"question": "What applies?"}


# --- the derivation is auditable ----------------------------------------------


def test_bound_is_derived_from_the_pool_constants() -> None:
    """Raising the pool without revisiting this fails here rather than silently
    reintroducing the deadlock.

    Asserted on the **derived** value, not the shipped one: the shipped value is
    held below it pending KD-10's successor (2026-08-05), and folding the hold
    into this assertion would make a temporary decision look like arithmetic.
    """
    assert DERIVED_MAX_CONCURRENT_QUERIES == (POOL_SIZE + POOL_MAX_OVERFLOW - RESERVED) // (
        CONNECTIONS_PER_QUERY
    )
    assert DERIVED_MAX_CONCURRENT_QUERIES == 8  # (5 + 5 - 2) // 1, at today's constants


def test_reserved_is_an_enumeration_not_a_magic_margin() -> None:
    """The margin is a named list whose length *is* the constant, so adding a
    consumer without adjusting the arithmetic is impossible."""
    assert len(RESERVED_CONNECTIONS) == RESERVED
    assert set(RESERVED_CONNECTIONS) == {"query_log write", "/health readiness probe"}
    assert all(name.strip() for name in RESERVED_CONNECTIONS)


def test_bound_tracks_a_hypothetical_pool_change() -> None:
    assert max_concurrent_queries(pool_size=20, max_overflow=20) == 38
    assert max_concurrent_queries(pool_size=1, max_overflow=0) == 1  # never zero


def test_divisor_is_an_enumeration_not_a_magic_number() -> None:
    """RESERVED guards the numerator; this guards the divisor, which is where
    KD-10's deferred risk actually lives."""
    assert len(QUERY_CONNECTIONS) == CONNECTIONS_PER_QUERY
    assert all(name.strip() for name in QUERY_CONNECTIONS)


async def test_connections_per_query_is_measured_against_a_live_pool(
    pooled_engine: AsyncEngine, seeded_corpus: SeededCorpus
) -> None:
    """The divisor asserted against the code it describes, not against itself.

    KD-10 defers a specific risk: "if anything ever adds a second concurrent
    connection consumer to a request, the arithmetic silently changes and the
    deadlock returns." An equality between two constants cannot catch that — only
    counting real checkouts can. Adding a session anywhere inside `retrieve()`
    fails here, which forces the arithmetic to be revisited rather than silently
    invalidated.

    **Total checkouts, not peak overlap, and the difference is the whole test.**
    Peak is what the arithmetic nominally cares about, but it is measured under
    whatever interleaving the event loop happens to produce: a third session
    whose connection has to be *created* can land after an earlier branch has
    already checked in, and the observed peak stays at 2 while three sessions
    were opened. That reading is timing luck, and a test that reports it would
    pass while proving nothing. Total checkouts is deterministic and
    conservative — it can only over-count concurrency, which errs toward a
    smaller bound, and any added session forces the question to be looked at.
    """
    checkouts = 0
    live = 0
    peak = 0

    def on_checkout(*_: Any) -> None:
        nonlocal checkouts, live, peak
        checkouts += 1
        live += 1
        peak = max(peak, live)

    def on_checkin(*_: Any) -> None:
        nonlocal live
        live -= 1

    sync_engine = pooled_engine.sync_engine
    event.listen(sync_engine, "checkout", on_checkout)
    event.listen(sync_engine, "checkin", on_checkin)
    try:
        factory = async_sessionmaker(pooled_engine, expire_on_commit=False)
        retriever = Retriever(factory, StubQueryEmbedder())
        results = await retriever.retrieve(PROBE_QUERY, k=5)
    finally:
        event.remove(sync_engine, "checkout", on_checkout)
        event.remove(sync_engine, "checkin", on_checkin)

    assert results, "a retrieval that returned nothing proves nothing about its pool use"
    assert checkouts == CONNECTIONS_PER_QUERY, (
        f"one retrieve() checked out {checkouts} connections; the semaphore divides by "
        f"{CONNECTIONS_PER_QUERY}. Update QUERY_CONNECTIONS and re-derive the bound."
    )
    assert peak <= CONNECTIONS_PER_QUERY


# --- the bound is enforced ----------------------------------------------------


async def test_bound_admits_max_concurrent_and_sheds_the_next() -> None:
    """Retrieval is slowed deliberately so the overlap window is deterministic
    rather than timing luck (SPEC-004 AC-7's pattern)."""
    app = build_app(
        StubRetriever(delay=0.4),
        query_acquire_timeout_seconds=0.05,
        max_concurrent_queries=2,
    )
    async with client_for(app) as http:
        responses = await asyncio.gather(
            *[http.post("/query", json=QUESTION, timeout=10) for _ in range(3)]
        )
    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 200, 503]

    shed = next(r for r in responses if r.status_code == 503)
    assert shed.json()["error"]["code"] == "overloaded"
    # A fast, honest, retryable answer instead of a 30-second hang.
    assert int(shed.headers["retry-after"]) >= 1


async def test_slot_is_released_before_generation() -> None:
    """The semaphore guards the pool, not the request: retrieval's connections
    are released before the provider call, and generation holds none. Two
    requests whose *generation* overlaps must both succeed at a bound of 1."""
    app = build_app(StubRetriever(delay=0.02), max_concurrent_queries=1)
    async with client_for(app) as http:
        responses = await asyncio.gather(
            *[http.post("/query", json=QUESTION, timeout=10) for _ in range(4)]
        )
    assert [response.status_code for response in responses] == [200] * 4


# --- the deadlock the bound exists to prevent ---------------------------------


@pytest.fixture
async def tiny_pool_engine() -> AsyncIterator[AsyncEngine]:
    """A pool small enough to deadlock quickly, with a 1s timeout so the
    regression test costs a second rather than thirty."""
    engine = create_async_engine(DATABASE_URL, pool_size=2, max_overflow=0, pool_timeout=1)
    yield engine
    await engine.dispose()


async def test_two_connections_per_request_can_exhaust_the_pool(
    tiny_pool_engine: AsyncEngine,
) -> None:
    """KD-10's premise, demonstrated rather than asserted in the abstract.

    Each task takes its *first* connection, exhausting the pool, and then all
    wait for a second that nobody can release — the shape SPEC-004's two-session
    retrieve() has, and the reason the semaphore exists.
    """
    total = 2  # == pool_size, so every task can hold one and want another
    started = asyncio.Event()
    held = 0

    async def double_checkout() -> None:
        nonlocal held
        async with tiny_pool_engine.connect() as first:
            await first.execute(text("SELECT 1"))
            held += 1
            if held >= total:
                started.set()
            await started.wait()
            async with tiny_pool_engine.connect() as second:  # nobody can grant this
                await second.execute(text("SELECT 1"))

    results = await asyncio.gather(
        *[double_checkout() for _ in range(total)], return_exceptions=True
    )
    assert all(isinstance(result, Exception) for result in results), (
        "expected pool-timeout failures — the deadlock the semaphore prevents"
    )
    assert any("timed out" in str(result).lower() for result in results)


async def test_semaphore_keeps_in_flight_retrievals_under_the_pool_bound() -> None:
    """The invariant that makes the deadlock unreachable: concurrent retrievals
    x connections-per-query never exceeds the pool."""
    peak = 0
    live = 0

    class CountingRetriever(StubRetriever):
        async def retrieve(self, query, k=8, filters=None):  # type: ignore[no-untyped-def]
            nonlocal peak, live
            live += 1
            peak = max(peak, live)
            try:
                await asyncio.sleep(0.05)
                return await super().retrieve(query, k, filters)
            finally:
                live -= 1

    app = build_app(CountingRetriever(), query_acquire_timeout_seconds=10)
    async with client_for(app, READ_KEY) as http:
        await asyncio.gather(*[http.post("/query", json=QUESTION, timeout=30) for _ in range(12)])

    assert peak <= MAX_CONCURRENT_QUERIES
    assert peak * CONNECTIONS_PER_QUERY <= POOL_SIZE + POOL_MAX_OVERFLOW - RESERVED


def test_the_shipped_bound_is_held_below_the_derived_one() -> None:
    """The derivation says 8; the service uses 4, and the gap is deliberate.

    **Correct arithmetic with an absent reason is not a number to ship.** The
    divisor went 2 -> 1 when the second branch was deleted (SPEC-004 KD-17),
    which is exactly what the enumeration was built to make visible — and the
    same deletion removed the deadlock the bound existed to prevent. What the
    bound is *for* is now an open question (SPEC-006 KD-10 amendment 6,
    Proposed), so the shipped value is pinned until it is answered.

    Asserting both numbers keeps the hold honest: if the derivation changes,
    this fails and the hold has to be re-examined rather than silently masking
    it.
    """
    assert DERIVED_MAX_CONCURRENT_QUERIES == 8
    assert MAX_CONCURRENT_QUERIES == 4
    assert MAX_CONCURRENT_QUERIES <= DERIVED_MAX_CONCURRENT_QUERIES

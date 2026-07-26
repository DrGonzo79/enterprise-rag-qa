"""Monthly cap, derived daily ceiling, and the circuit breaker (SPEC-006 AC-14,
KD-16)."""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from api_harness import build_app, post, settings
from rag_qa.api.budget import (
    CENT,
    SpendGuard,
    derive_daily_limit,
    next_utc_month_start,
    seconds_until_utc_midnight,
    seconds_until_utc_month_end,
    utc_day_start,
    utc_month_start,
)
from rag_qa.api.deps import ConfigurationError
from rag_qa.db.models import QueryLog
from rag_qa.generation.prompt import PROMPT_VERSION
from test_generation_service import FakeLLMClient

QUESTION = "What applies?"


def assert_no_figures(message: str) -> None:
    """The error body must name no dollar amounts.

    SPEC-006 KD-8 keeps the cost meter behind the admin key because an
    unauthenticated spend number is a live progress bar for anyone trying to
    drain the budget. A 503 body naming the ceiling, the override, and the
    derived value was that same meter, reachable by any caller who can trigger
    the error — a side channel around the scope that was carefully chosen.
    """
    assert "$" not in message, message
    assert not re.search(r"\d+\.\d{2}", message), message


async def _log_spend(
    factory: async_sessionmaker[AsyncSession], marker: str, cost: str, created_at: datetime
) -> None:
    async with factory() as session:
        session.add(
            QueryLog(
                id=uuid.uuid4(),
                question=f"{marker} {created_at.isoformat()}",
                provider="anthropic",
                model="claude-sonnet-5",
                latency_ms=10,
                prompt_tokens=1000,
                completion_tokens=100,
                cost_usd=Decimal(cost),
                retrieved_chunk_ids=[],
                answer_text="x",
                verdict="answered",
                prompt_version=PROMPT_VERSION,
                created_at=created_at,
            )
        )
        await session.commit()


async def _cleanup(factory: async_sessionmaker[AsyncSession], marker: str) -> None:
    async with factory() as session:
        await session.execute(
            text("DELETE FROM query_log WHERE question LIKE :q"), {"q": f"{marker}%"}
        )
        await session.commit()


# --- off by default -----------------------------------------------------------


async def test_disabled_guard_opens_no_connection() -> None:
    guard = SpendGuard(None, daily_limit_usd=None)
    assert not guard.enabled
    await guard.check()
    assert guard.refresh_count == 0


async def test_query_serves_normally_with_no_ceiling_configured() -> None:
    app = build_app()
    assert (await post(app, "/query", {"question": QUESTION})).status_code == 200


# --- the breaker --------------------------------------------------------------


async def test_breaker_trips_before_the_provider_call(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A breaker that trips after paying for the answer is not a breaker."""
    marker = f"budget-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "9.00", datetime.now(UTC))
    client = FakeLLMClient()
    app = build_app(
        client=client,
        session_factory=session_factory,
        daily_budget_usd=Decimal("5.00"),
    )
    try:
        response = await post(app, "/query", {"question": QUESTION})
    finally:
        await _cleanup(session_factory, marker)

    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "budget_exhausted"
    assert client.calls == []  # never reached the provider
    assert int(response.headers["retry-after"]) > 0


async def test_breaker_is_an_error_envelope_not_a_canned_answer(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A canned answer in the shape of a real one would teach a viewer that the
    system answered when it did not — and would land in query_log as an answer
    no model produced."""
    marker = f"budget-{uuid.uuid4()}"
    question = f"refused-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "9.00", datetime.now(UTC))
    app = build_app(session_factory=session_factory, daily_budget_usd=Decimal("5.00"))
    try:
        response = await post(app, "/query", {"question": question})
        async with session_factory() as session:
            rows = (
                await session.execute(
                    text("SELECT count(*) FROM query_log WHERE question = :q"), {"q": question}
                )
            ).scalar_one()
    finally:
        await _cleanup(session_factory, marker)

    assert "verdict" not in response.json()
    assert "error" in response.json()
    assert rows == 0  # no row written for a request that never happened


async def test_spend_is_scoped_to_the_utc_day(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    marker = f"budget-{uuid.uuid4()}"
    question = f"{marker} probe"
    yesterday = utc_day_start(datetime.now(UTC)) - timedelta(hours=1)
    await _log_spend(session_factory, marker, "9.00", yesterday)
    app = build_app(session_factory=session_factory, daily_budget_usd=Decimal("5.00"))
    try:
        # Yesterday's spend does not count against today's ceiling.
        assert (await post(app, "/query", {"question": question})).status_code == 200
        await _log_spend(session_factory, marker, "9.00", datetime.now(UTC))
        app_today = build_app(session_factory=session_factory, daily_budget_usd=Decimal("5.00"))
        assert (await post(app_today, "/query", {"question": question})).status_code == 503
    finally:
        # The successful call above logged its own row under the same marker.
        await _cleanup(session_factory, marker)


# --- caching ------------------------------------------------------------------


async def test_one_aggregate_per_ttl_window_not_one_per_request(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The property that keeps the ceiling off the hot path."""
    guard = SpendGuard(session_factory, daily_limit_usd=Decimal("100"), refresh_seconds=1000.0)
    for _ in range(5):
        await guard.check()
    assert guard.refresh_count == 1


async def test_local_delta_trips_inside_the_same_ttl_window(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Spend since the last refresh counts immediately — otherwise the breaker
    would wait a full TTL after crossing the limit, and the overshoot would be
    unbounded rather than bounded by spend_rate x TTL x replicas."""
    from rag_qa.api.errors import BudgetExhausted

    guard = SpendGuard(session_factory, daily_limit_usd=Decimal("1.00"), refresh_seconds=1000.0)
    await guard.check()  # under the limit
    guard.record(Decimal("1.50"))
    with pytest.raises(BudgetExhausted):
        await guard.check()
    assert guard.refresh_count == 1  # no extra query needed to notice


def test_retry_after_counts_down_to_utc_midnight() -> None:
    just_before = datetime(2026, 7, 26, 23, 59, 0, tzinfo=UTC)
    assert seconds_until_utc_midnight(just_before) == 60
    assert seconds_until_utc_midnight(datetime(2026, 7, 26, 0, 0, 0, tzinfo=UTC)) == 86400


# --- the monthly cap, and the daily ceiling derived from it --------------------


@pytest.mark.parametrize(
    ("year", "month", "days"),
    [(2026, 2, 28), (2028, 2, 29), (2026, 4, 30), (2026, 7, 31)],
)
def test_every_month_length_divides_and_never_overspends(year: int, month: int, days: int) -> None:
    """28/29/30/31, including a leap February — the divisor is this month's real
    length, so a full month at the ceiling lands on the budget rather than past
    it in short months and short of it in long ones."""
    at = datetime(year, month, 5, tzinfo=UTC)
    derived = derive_daily_limit(Decimal("20.00"), at)
    assert derived * days <= Decimal("20.00")
    # And close to it: flooring to the cent may cost at most a cent per day.
    assert derived * days > Decimal("20.00") - (CENT * days)
    # The month window is exactly as long as the month.
    assert (next_utc_month_start(at) - utc_month_start(at)).days == days
    assert seconds_until_utc_month_end(utc_month_start(at)) == days * 86400


def test_utc_is_the_definition_so_dst_cannot_move_the_boundary() -> None:
    """The rollover is UTC, everywhere. UTC observes no daylight saving, so every
    budget day is exactly 86400 seconds — a local-time ceiling would have a
    23-hour day and a 25-hour day each year, one silently tightening the ceiling
    and the other silently loosening it."""
    ny = ZoneInfo("America/New_York")
    berlin = ZoneInfo("Europe/Berlin")

    # US spring-forward 2026-03-08 02:00 local; EU 2026-03-29 02:00 local.
    for local_now in (
        datetime(2026, 3, 8, 1, 59, tzinfo=ny),  # minutes before the US shift
        datetime(2026, 3, 8, 3, 1, tzinfo=ny),  # minutes after
        datetime(2026, 11, 1, 1, 30, tzinfo=ny),  # US fall-back, ambiguous local hour
        datetime(2026, 3, 29, 1, 59, tzinfo=berlin),
        datetime(2026, 3, 29, 3, 1, tzinfo=berlin),
    ):
        day = utc_day_start(local_now)
        assert day.tzinfo == UTC
        assert day == local_now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        # Every budget day is 86400 seconds, across every transition.
        assert seconds_until_utc_midnight(day) == 86400

    # The same instant expressed in three zones yields one identical window.
    instant = datetime(2026, 3, 8, 12, 0, tzinfo=UTC)
    assert (
        utc_day_start(instant)
        == utc_day_start(instant.astimezone(ny))
        == utc_day_start(instant.astimezone(berlin))
    )


def test_a_naive_datetime_is_rejected_rather_than_assumed() -> None:
    """`astimezone()` reads a naive datetime as system-local, which shifts the
    whole ceiling by the host's UTC offset — a wrong window that looks right on
    a UTC developer machine and is wrong in deployment."""
    with pytest.raises(ValueError, match="aware datetime"):
        utc_day_start(datetime(2026, 7, 26, 12, 0))
    with pytest.raises(ValueError, match="aware datetime"):
        utc_month_start(datetime(2026, 7, 26, 12, 0))


def test_day_boundary_is_exact_at_the_edges() -> None:
    assert utc_day_start(datetime(2026, 7, 26, 0, 0, 0, tzinfo=UTC)) == datetime(
        2026, 7, 26, tzinfo=UTC
    )
    assert utc_day_start(datetime(2026, 7, 26, 23, 59, 59, 999999, tzinfo=UTC)) == datetime(
        2026, 7, 26, tzinfo=UTC
    )
    # One microsecond into the next day rolls the window over.
    assert utc_day_start(datetime(2026, 7, 27, 0, 0, 0, tzinfo=UTC)) == datetime(
        2026, 7, 27, tzinfo=UTC
    )
    # Month rollover at the year boundary.
    assert utc_month_start(datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == datetime(
        2026, 12, 1, tzinfo=UTC
    )
    assert next_utc_month_start(datetime(2028, 2, 29, tzinfo=UTC)) == datetime(
        2028, 3, 1, tzinfo=UTC
    )


def test_daily_is_derived_from_the_monthly_budget() -> None:
    """The monthly figure is the one an owner commits to; the daily ceiling is a
    consequence of it, not an independent choice with a surprise attached."""
    # 31 days: $20 / 31 = $0.645..., floored to the cent.
    assert derive_daily_limit(Decimal("20.00"), datetime(2026, 7, 26, tzinfo=UTC)) == Decimal(
        "0.64"
    )
    # 28 days: the divisor is this month's length, not a nominal 30, so a full
    # month at the ceiling lands on the budget rather than past it.
    assert derive_daily_limit(Decimal("28.00"), datetime(2026, 2, 10, tzinfo=UTC)) == Decimal(
        "1.00"
    )
    # A month at the derived ceiling never exceeds the monthly budget.
    for month, days in ((1, 31), (2, 28), (4, 30)):
        derived = derive_daily_limit(Decimal("20.00"), datetime(2026, month, 5, tzinfo=UTC))
        assert derived * days <= Decimal("20.00")


def test_derived_daily_never_rounds_to_zero() -> None:
    """A budget too small to divide should refuse loudly, not serve as if
    unconfigured — a zero ceiling that never trips is the failure mode this
    whole decision exists to avoid."""
    assert derive_daily_limit(Decimal("0.10"), datetime(2026, 7, 26, tzinfo=UTC)) == Decimal("0.01")


def test_explicit_daily_override_is_capped_at_twice_the_derived_ceiling() -> None:
    """An override is a burst shape, not a second budget. Uncapped, $5/day
    against a $20 month drains it in four days with no error until the monthly
    trips — the ceiling failing at exactly its job."""
    at = datetime(2026, 7, 26, tzinfo=UTC)  # 31 days: derived $0.64, cap $1.28
    derived = SpendGuard(None, monthly_limit_usd=Decimal("20.00"))
    assert derived.daily_limit_for(at) == Decimal("0.64")

    # Below the derived ceiling: honored as given.
    under = SpendGuard(None, daily_limit_usd=Decimal("0.40"), monthly_limit_usd=Decimal("20.00"))
    assert under.daily_limit_for(at) == Decimal("0.40")

    # Between derived and the cap: honored — a deliberate launch-day burst.
    burst = SpendGuard(None, daily_limit_usd=Decimal("1.00"), monthly_limit_usd=Decimal("20.00"))
    assert burst.daily_limit_for(at) == Decimal("1.00")

    # Above the cap: capped, not honored.
    footgun = SpendGuard(None, daily_limit_usd=Decimal("5.00"), monthly_limit_usd=Decimal("20.00"))
    effective = footgun.daily_limit_for(at)
    assert effective == Decimal("1.28") == 2 * Decimal("0.64")

    # The cap is what makes the drain time bounded: >= 15 days, not 4.
    assert effective is not None and Decimal("20.00") / effective > 15


def test_the_cap_boundary_is_inclusive() -> None:
    """Exactly 2x derived is honored; a cent above it is capped.

    Pinned explicitly because `min()` makes the boundary invisible in the code —
    a reader cannot tell from `min(daily, 2 * derived)` whether the operator who
    typed the cap value exactly gets what they typed, and that is precisely the
    value someone reading the documentation will type.
    """
    at = datetime(2026, 7, 26, tzinfo=UTC)  # 31 days: derived $0.64, cap $1.28
    exact = SpendGuard(None, daily_limit_usd=Decimal("1.28"), monthly_limit_usd=Decimal("20.00"))
    assert exact.daily_limit_for(at) == Decimal("1.28")
    # ...and it is reported as an honored burst, not as a capped value.
    assert "capped" not in exact._daily_shape(at)[1]

    over = SpendGuard(None, daily_limit_usd=Decimal("1.29"), monthly_limit_usd=Decimal("20.00"))
    assert over.daily_limit_for(at) == Decimal("1.28")
    assert "capped" in over._daily_shape(at)[1]


def test_the_warning_boundary_is_exclusive_of_derived() -> None:
    """At exactly the derived ceiling there is nothing to warn about — the
    override and the derivation agree. A cent above it, the operator has chosen
    to spend the month faster than uniformly and should be told so."""
    monthly = Decimal("20.00")
    derived = derive_daily_limit(monthly, datetime.now(UTC))
    for override, expected_records in ((derived, 0), (derived + CENT, 1)):
        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = records.append  # type: ignore[method-assign]
        deps_logger = logging.getLogger("rag_qa.api.deps")
        deps_logger.addHandler(handler)
        try:
            settings(daily_budget_usd=override, monthly_budget_usd=monthly).require_serving(
                needs_providers=False
            )
        finally:
            deps_logger.removeHandler(handler)
        assert len(records) == expected_records, f"override ${override} vs derived ${derived}"


def test_the_cap_moves_with_the_month_length() -> None:
    """Derived is per-month, so the cap is too — February's is higher because
    its days are fewer, not because the budget changed."""
    guard = SpendGuard(None, daily_limit_usd=Decimal("99"), monthly_limit_usd=Decimal("28.00"))
    assert guard.daily_limit_for(datetime(2026, 2, 10, tzinfo=UTC)) == Decimal("2.00")  # 28 days
    assert guard.daily_limit_for(datetime(2026, 7, 10, tzinfo=UTC)) == Decimal("1.80")  # 31 days


def test_override_above_derived_warns_at_startup_naming_both_numbers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="rag_qa.api.deps"):
        settings(
            daily_budget_usd=Decimal("5.00"), monthly_budget_usd=Decimal("20.00")
        ).require_serving(needs_providers=False)

    fields = next(r for r in caplog.records if r.name == "rag_qa.api.deps").__dict__
    assert fields["override_usd"] == "5.00"
    assert fields["derived_usd"] == "0.64"
    assert fields["cap_usd"] == "1.28"
    assert fields["effective_usd"] == "1.28"
    assert fields["monthly_usd"] == "20.00"
    assert fields["min_days_to_drain_month"] >= 15


def test_no_warning_when_the_override_is_at_or_below_derived(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="rag_qa.api.deps"):
        settings(
            daily_budget_usd=Decimal("0.50"), monthly_budget_usd=Decimal("20.00")
        ).require_serving(needs_providers=False)
    assert [r for r in caplog.records if r.name == "rag_qa.api.deps"] == []


async def test_capped_override_is_the_ceiling_actually_enforced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The cap is enforced on the serving path, not merely reported at startup:
    spend above the cap but below the requested override still trips."""
    marker = f"budget-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "2.00", datetime.now(UTC))
    client = FakeLLMClient()
    app = build_app(
        client=client,
        session_factory=session_factory,
        daily_budget_usd=Decimal("5.00"),  # capped to ~2x derived, well under $2.00
        monthly_budget_usd=Decimal("20.00"),
    )
    try:
        response = await post(app, "/query", {"question": QUESTION})
    finally:
        await _cleanup(session_factory, marker)

    assert response.status_code == 503
    assert client.calls == []
    assert_no_figures(response.json()["error"]["message"])


def test_daily_above_monthly_is_rejected_at_startup() -> None:
    """Incoherent rather than merely generous: the day it is reached, the month
    is already over."""
    with pytest.raises(ConfigurationError, match="exceeds"):
        settings(
            daily_budget_usd=Decimal("50.00"), monthly_budget_usd=Decimal("20.00")
        ).require_serving(needs_providers=False)


async def test_monthly_cap_trips_over_http_before_the_provider_call(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    marker = f"budget-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "25.00", datetime.now(UTC))
    client = FakeLLMClient()
    app = build_app(
        client=client, session_factory=session_factory, monthly_budget_usd=Decimal("20.00")
    )
    try:
        response = await post(app, "/query", {"question": QUESTION})
    finally:
        await _cleanup(session_factory, marker)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "budget_exhausted"
    assert client.calls == []
    # Which ceiling tripped is operator information, not caller information.
    assert_no_figures(response.json()["error"]["message"])


async def test_monthly_window_sees_spend_the_daily_window_cannot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The exposure a daily ceiling structurally cannot bound: quiet days that
    add up. Spend sits at the start of the month and the clock is moved to
    mid-month, so today's window is empty by construction on any calendar date.
    """
    from rag_qa.api.errors import BudgetExhausted

    marker = f"budget-{uuid.uuid4()}"
    month_start = utc_month_start(datetime.now(UTC))
    mid_month = month_start + timedelta(days=15, hours=9)
    await _log_spend(session_factory, marker, "25.00", month_start)
    try:
        daily_only = SpendGuard(
            session_factory, daily_limit_usd=Decimal("5.00"), now=lambda: mid_month
        )
        await daily_only.check()  # today is clear — the daily ceiling sees nothing

        monthly = SpendGuard(
            session_factory, monthly_limit_usd=Decimal("20.00"), now=lambda: mid_month
        )
        with pytest.raises(BudgetExhausted) as raised:
            await monthly.check()
        assert raised.value.ceiling == "monthly"
    finally:
        await _cleanup(session_factory, marker)


async def test_spend_before_this_month_does_not_count(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    marker = f"budget-{uuid.uuid4()}"
    last_month = utc_month_start(datetime.now(UTC)) - timedelta(hours=1)
    await _log_spend(session_factory, marker, "500.00", last_month)
    app = build_app(session_factory=session_factory, monthly_budget_usd=Decimal("20.00"))
    try:
        response = await post(app, "/query", {"question": f"{marker} probe"})
    finally:
        await _cleanup(session_factory, marker)
    assert response.status_code == 200


async def test_both_windows_come_from_one_statement(pooled_engine: AsyncEngine) -> None:
    """Adding the monthly ceiling must not add a connection consumer: the budget
    refresh is deliberately *not* in RESERVED_CONNECTIONS, and concurrency.py's
    arithmetic assumes one single-checkout aggregate per TTL."""
    statements: list[str] = []

    def record(*args: Any) -> None:
        statement = args[2]
        if "query_log" in statement:
            statements.append(statement)

    guard = SpendGuard(
        async_sessionmaker(pooled_engine, expire_on_commit=False),
        daily_limit_usd=Decimal("100"),
        monthly_limit_usd=Decimal("1000"),
        refresh_seconds=1000.0,
    )
    event.listen(pooled_engine.sync_engine, "before_cursor_execute", record)
    try:
        for _ in range(3):
            await guard.check()
    finally:
        event.remove(pooled_engine.sync_engine, "before_cursor_execute", record)

    assert guard.refresh_count == 1
    assert len(statements) == 1, "the two windows must be one aggregate, not two"


async def test_monthly_is_reported_when_both_ceilings_are_exhausted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A Retry-After that expires into another 503 is worse than an honest long
    one — "resets at midnight" is false when the month is gone."""
    marker = f"budget-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "25.00", datetime.now(UTC))
    app = build_app(session_factory=session_factory, monthly_budget_usd=Decimal("20.00"))
    try:
        response = await post(app, "/query", {"question": QUESTION})
    finally:
        await _cleanup(session_factory, marker)

    assert response.status_code == 503
    # The monthly reset is what is reported, and it is reported as a clock the
    # caller can act on rather than as a ceiling they can meter.
    assert_no_figures(response.json()["error"]["message"])
    now = datetime.now(UTC)
    assert int(response.headers["retry-after"]) == pytest.approx(
        seconds_until_utc_month_end(now), abs=5
    )


def test_retry_after_counts_down_to_the_next_utc_month() -> None:
    assert seconds_until_utc_month_end(datetime(2026, 7, 31, 23, 59, 0, tzinfo=UTC)) == 60
    assert next_utc_month_start(datetime(2026, 12, 15, tzinfo=UTC)) == datetime(
        2027, 1, 1, tzinfo=UTC
    )
    assert next_utc_month_start(datetime(2026, 1, 31, tzinfo=UTC)) == datetime(
        2026, 2, 1, tzinfo=UTC
    )


async def test_the_503_body_names_no_figures_and_the_log_names_all_of_them(
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The reconciliation: the caller learns *that* and *when*, the operator
    learns *how much*. Both from one trip."""
    marker = f"budget-{uuid.uuid4()}"
    await _log_spend(session_factory, marker, "9.00", datetime.now(UTC))
    app = build_app(session_factory=session_factory, daily_budget_usd=Decimal("5.00"))
    try:
        with caplog.at_level(logging.WARNING, logger="rag_qa.api.budget"):
            response = await post(app, "/query", {"question": QUESTION})
    finally:
        await _cleanup(session_factory, marker)

    error = response.json()["error"]
    assert_no_figures(error["message"])
    assert "ceiling" not in error["message"]  # which window tripped is operator info
    # ...and the presentation seam tells a client to render the explanatory
    # state rather than an error page, without keeping its own list.
    assert error["presentation"] == "explanatory"
    assert error["reset"] == "window"
    assert int(response.headers["retry-after"]) > 0

    fields = next(r for r in caplog.records if r.msg == "spend ceiling reached").__dict__
    assert fields["ceiling"] == "daily"
    assert fields["limit_usd"] == "5.00"
    assert Decimal(fields["spent_usd"]) >= Decimal("9.00")
    assert fields["resets_at"]
    assert fields["request_id"] == response.headers["x-request-id"]

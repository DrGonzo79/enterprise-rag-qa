"""Daily spend ceiling with a circuit breaker (SPEC-006 KD-16).

A public demo URL in front of a metered API is a live financial exposure, and the
operational failure is worse than the financial one: a drained quota takes the
demo down precisely when someone is clicking it.

The ceiling bounds the quantity actually at risk — dollars. It must be **shared
across replicas** (three replicas each enforcing $5 would enforce $15), so the
authoritative total comes from `query_log`; it is cached with a short TTL so the
hot path does not pay a query per request, and the in-process delta since the
last refresh is added, so the breaker trips inside the window that crosses the
limit rather than waiting for a refresh. The resulting overshoot is bounded and
computable — at most spend_rate x TTL x replicas — rather than hoped for.

Tripping produces 503 `budget_exhausted`, never a canned answer: under the
ceiling the question was never asked, so this is transport-level (KD-1), and a
fake answer would teach a viewer that the system answered when it did not.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_qa.api.errors import BudgetExhausted

_SPEND_SINCE = text("SELECT coalesce(sum(cost_usd), 0) FROM query_log WHERE created_at >= :since")


def utc_day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def seconds_until_utc_midnight(now: datetime) -> int:
    reset = utc_day_start(now) + timedelta(days=1)
    return max(1, int((reset - now.astimezone(UTC)).total_seconds()))


class SpendGuard:
    """Off unless a limit is configured; when off it opens no connection."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None,
        *,
        daily_limit_usd: Decimal | None,
        refresh_seconds: float = 30.0,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._limit = daily_limit_usd
        self._refresh_seconds = refresh_seconds
        self._now = now
        self._monotonic = monotonic
        self._cached_total = Decimal("0")
        self._local_delta = Decimal("0")
        self._refreshed_at: float | None = None
        self._cached_day: datetime | None = None
        # Exposed so AC-14 can assert one aggregate per TTL window, not one per
        # request — the property that keeps this off the hot path.
        self.refresh_count = 0

    @property
    def enabled(self) -> bool:
        return self._limit is not None and self._session_factory is not None

    async def check(self) -> None:
        """Raise BudgetExhausted if today's spend has reached the ceiling."""
        if self._limit is None or self._session_factory is None:
            return

        now = self._now()
        day = utc_day_start(now)
        stale = (
            self._refreshed_at is None
            or self._cached_day != day
            or (self._monotonic() - self._refreshed_at) >= self._refresh_seconds
        )
        if stale:
            await self._refresh(day)

        if self._cached_total + self._local_delta >= self._limit:
            retry_after = seconds_until_utc_midnight(now)
            raise BudgetExhausted(
                f"daily demo budget of ${self._limit} reached; resets at "
                f"{(day + timedelta(days=1)).isoformat()}",
                retry_after=retry_after,
            )

    def record(self, cost_usd: Decimal) -> None:
        """Count spend that has not reached query_log's cached total yet."""
        self._local_delta += cost_usd

    async def _refresh(self, day: datetime) -> None:
        assert self._session_factory is not None
        async with self._session_factory() as session:
            total = (await session.execute(_SPEND_SINCE, {"since": day})).scalar_one()
        self._cached_total = Decimal(str(total))
        self._local_delta = Decimal("0")
        self._cached_day = day
        self._refreshed_at = self._monotonic()
        self.refresh_count += 1

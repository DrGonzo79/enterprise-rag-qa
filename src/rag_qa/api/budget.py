"""Monthly spend cap and the daily ceiling derived from it (SPEC-006 KD-16).

A public demo URL in front of a metered API is a live financial exposure, and the
operational failure is worse than the financial one: a drained quota takes the
demo down precisely when someone is clicking it.

**The monthly budget is the input; the daily ceiling is derived from it.** A
daily ceiling chosen on its own has a monthly consequence nobody signed up for —
$5/day is ~$150/month, indefinitely, on a personal project. The number an owner
can actually commit to is the monthly one, so that is what is configured, and
the daily ceiling is `monthly / days-in-this-UTC-month` unless overridden. The
daily ceiling shapes the *burst*; the monthly cap is the bound that matters.

Both ceilings bound the quantity actually at risk — dollars. They must be
**shared across replicas** (three replicas each enforcing $5 would enforce $15),
so the authoritative totals come from `query_log`; they are cached with a short
TTL so the hot path does not pay a query per request, and the in-process delta
since the last refresh is added, so the breaker trips inside the window that
crosses the limit rather than waiting for a refresh. The resulting overshoot is
bounded and computable — at most spend_rate x TTL x replicas — rather than hoped
for. Both windows come from **one** statement, so the second ceiling costs no
second connection (the refresh is not in `RESERVED_CONNECTIONS`).

Tripping produces 503 `budget_exhausted`, never a canned answer: under the
ceiling the question was never asked, so this is transport-level (KD-1), and a
fake answer would teach a viewer that the system answered when it did not.
"""

import calendar
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_qa.api.conditions import spec_for
from rag_qa.api.errors import BudgetExhausted

logger = logging.getLogger(__name__)

# One statement, one checkout: the daily total is a filtered aggregate over the
# month's rows rather than a second query. Adding the monthly ceiling must not
# add a connection consumer — see concurrency.RESERVED_CONNECTIONS.
_SPEND_WINDOWS = text(
    "SELECT coalesce(sum(cost_usd) FILTER (WHERE created_at >= :day), 0) AS day_total, "
    "       coalesce(sum(cost_usd), 0) AS month_total "
    "FROM query_log WHERE created_at >= :month"
)

CENT = Decimal("0.01")

# How far above the derived daily ceiling an explicit override may push. An
# override is a burst shape for a launch day, not a second budget: at 2x, the
# fastest a month can be drained is ~15 days, which is long enough that the
# monthly cap tripping is a signal rather than a surprise. Above this the value
# is capped, not honored — an override that can consume the month in under a
# week with no error until the monthly trips is a footgun, and silently
# accepting it would be the ceiling failing at exactly its job.
MAX_DAILY_BURST_MULTIPLE = 2


def utc_day_start(now: datetime) -> datetime:
    """Start of `now`'s UTC day.

    **UTC is the definition, everywhere, and that is what makes the rollover
    boring.** UTC observes no daylight saving, so every budget day is exactly
    86400 seconds and every month boundary is a fixed instant — a local-time
    ceiling would have a 23-hour day and a 25-hour day each year, one of which
    silently tightens the ceiling and the other silently loosens it. `query_log`
    stores `created_at` as `timestamptz`, so the window comparison is between
    absolute instants and does not depend on the database server's timezone
    either.

    An aware datetime in any zone converts correctly. A **naive** one is
    rejected rather than assumed: `astimezone()` would read it as system-local,
    which silently shifts the window by the host's UTC offset — a wrong ceiling
    that looks right everywhere except in production.
    """
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError(
            "budget windows require an aware datetime; a naive one would be read as "
            "system-local time and shift the ceiling by the host's UTC offset"
        )
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def utc_month_start(now: datetime) -> datetime:
    return utc_day_start(now).replace(day=1)


def next_utc_month_start(now: datetime) -> datetime:
    start = utc_month_start(now)
    return (start + timedelta(days=32)).replace(day=1)


def seconds_until_utc_midnight(now: datetime) -> int:
    reset = utc_day_start(now) + timedelta(days=1)
    return max(1, int((reset - now.astimezone(UTC)).total_seconds()))


def seconds_until_utc_month_end(now: datetime) -> int:
    reset = next_utc_month_start(now)
    return max(1, int((reset - now.astimezone(UTC)).total_seconds()))


def derive_daily_limit(monthly_limit_usd: Decimal, now: datetime) -> Decimal:
    """The daily ceiling implied by a monthly budget.

    Divided by the days in *this* month rather than a nominal 30, so a full month
    spent at the daily ceiling lands on the monthly budget rather than 3% past
    it. Floored to the cent, and never below one cent — a budget so small that
    the derived daily rounds to zero should refuse every request loudly rather
    than serve as if unconfigured.
    """
    days = calendar.monthrange(now.astimezone(UTC).year, now.astimezone(UTC).month)[1]
    return max(CENT, (monthly_limit_usd / days).quantize(CENT, rounding=ROUND_DOWN))


class SpendGuard:
    """Off unless a limit is configured; when off it opens no connection."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None,
        *,
        daily_limit_usd: Decimal | None = None,
        monthly_limit_usd: Decimal | None = None,
        refresh_seconds: float = 30.0,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._daily_limit = daily_limit_usd
        self._monthly_limit = monthly_limit_usd
        self._refresh_seconds = refresh_seconds
        self._now = now
        self._monotonic = monotonic
        self._day_total = Decimal("0")
        self._month_total = Decimal("0")
        self._local_delta = Decimal("0")
        self._refreshed_at: float | None = None
        self._cached_day: datetime | None = None
        self._cached_month: datetime | None = None
        # Exposed so AC-14 can assert one aggregate per TTL window, not one per
        # request — the property that keeps this off the hot path.
        self.refresh_count = 0

    @property
    def enabled(self) -> bool:
        configured = self._daily_limit is not None or self._monthly_limit is not None
        return configured and self._session_factory is not None

    def daily_limit_for(self, now: datetime) -> Decimal | None:
        """The daily ceiling in force, with an override capped at 2x derived."""
        return self._daily_shape(now)[0]

    def _daily_shape(self, now: datetime) -> tuple[Decimal | None, str]:
        """The ceiling and where it came from, so a 503 can say which."""
        if self._monthly_limit is None:
            return self._daily_limit, ""
        derived = derive_daily_limit(self._monthly_limit, now)
        if self._daily_limit is None:
            return derived, f" (from ${self._monthly_limit}/month)"
        capped = min(self._daily_limit, MAX_DAILY_BURST_MULTIPLE * derived)
        if capped < self._daily_limit:
            return capped, (
                f" (override ${self._daily_limit} capped at {MAX_DAILY_BURST_MULTIPLE}x the "
                f"${derived} derived from ${self._monthly_limit}/month)"
            )
        return capped, f" (burst override; ${derived} derived from ${self._monthly_limit}/month)"

    async def check(self) -> None:
        """Raise BudgetExhausted if either ceiling has been reached."""
        if not self.enabled:
            return
        assert self._session_factory is not None

        now = self._now()
        day, month = utc_day_start(now), utc_month_start(now)
        stale = (
            self._refreshed_at is None
            or self._cached_day != day
            or self._cached_month != month
            or (self._monotonic() - self._refreshed_at) >= self._refresh_seconds
        )
        if stale:
            await self._refresh(day, month)

        # The monthly ceiling is checked first: when both are exhausted, telling
        # a visitor "resets at midnight" would be false, and a Retry-After that
        # expires into another 503 is worse than an honest long one.
        if self._monthly_limit is not None and self._month_total + self._local_delta >= (
            self._monthly_limit
        ):
            self._trip(
                ceiling="monthly",
                limit=self._monthly_limit,
                spent=self._month_total + self._local_delta,
                origin="",
                resets_at=next_utc_month_start(now),
                retry_after=seconds_until_utc_month_end(now),
            )

        daily_limit, origin = self._daily_shape(now)
        if daily_limit is not None and self._day_total + self._local_delta >= daily_limit:
            self._trip(
                ceiling="daily",
                limit=daily_limit,
                spent=self._day_total + self._local_delta,
                origin=origin,
                resets_at=day + timedelta(days=1),
                retry_after=seconds_until_utc_midnight(now),
            )

    def _trip(
        self,
        *,
        ceiling: str,
        limit: Decimal,
        spent: Decimal,
        origin: str,
        resets_at: datetime,
        retry_after: int,
    ) -> None:
        """Figures to the log, a figure-free message to the caller.

        The ceiling, the override, the derived value, and the running total are
        exactly the cost meter SPEC-006 KD-8 refuses to publish — an error body
        naming them hands a caller a live progress bar toward draining the
        budget, and is a side channel around the admin scope on `/metrics`. The
        caller is told *that* the demo is not answering and *when* it resumes,
        which is everything they can act on.
        """
        logger.warning(
            "spend ceiling reached",
            extra={
                "ceiling": ceiling,
                "limit_usd": str(limit),
                "spent_usd": str(spent),
                "origin": origin.strip() or "configured",
                "resets_at": resets_at.isoformat(),
            },
        )
        raise BudgetExhausted(
            f"{spec_for('budget_exhausted').public_message}; it resets at {resets_at.isoformat()}",
            retry_after=retry_after,
            ceiling=ceiling,
        )

    def remaining(self, now: datetime) -> dict[str, Decimal]:
        """Headroom per configured ceiling, for the admin-scoped gauge."""
        headroom: dict[str, Decimal] = {}
        daily_limit, _ = self._daily_shape(now)
        if daily_limit is not None:
            headroom["daily"] = max(Decimal("0"), daily_limit - self._day_total - self._local_delta)
        if self._monthly_limit is not None:
            headroom["monthly"] = max(
                Decimal("0"), self._monthly_limit - self._month_total - self._local_delta
            )
        return headroom

    def record(self, cost_usd: Decimal) -> None:
        """Count spend that has not reached query_log's cached totals yet."""
        self._local_delta += cost_usd

    async def _refresh(self, day: datetime, month: datetime) -> None:
        assert self._session_factory is not None
        async with self._session_factory() as session:
            row = (await session.execute(_SPEND_WINDOWS, {"day": day, "month": month})).one()
        self._day_total = Decimal(str(row.day_total))
        self._month_total = Decimal(str(row.month_total))
        self._local_delta = Decimal("0")
        self._cached_day = day
        self._cached_month = month
        self._refreshed_at = self._monotonic()
        self.refresh_count += 1

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
crosses the limit rather than waiting for a refresh. Both windows come from
**one** statement, so the second ceiling costs no second connection (the refresh
is not in `RESERVED_CONNECTIONS`).

**Three quantities, not two, and the third is what makes the bound exact**
(KD-16 amendment 5). The cached total is what `query_log` said at the last
refresh; the recorded delta is what this process has spent since; and the
*reservation* is what this process has committed to spend but not yet spent. A
check that counted only the first two was blind to every request between its
budget check and its provider response — which is every request currently being
answered. That blindness is not bounded by the TTL (it exists at TTL zero) and
is not bounded by KD-10's semaphore (released before the provider call by
design), so it grew with arrival rate against a ceiling of cents. With the
reservation counted, **one replica can spend at most `ceiling + one worst-case
query`**, computable from `max_tokens` and the rate table with no term that
grows with traffic. The remaining overshoot is cross-replica staleness —
`(N-1) x TTL x arrival rate x per-query cost` — which is zero at today's single
container and is deferred to SPEC-010 with the deploy that would create it.

Tripping produces 503, never a canned answer: under the ceiling the question was
never asked, so this is transport-level (KD-1), and a fake answer would teach a
viewer that the system answered when it did not. **Which** 503 depends on what
crossed the line: `budget_exhausted` when the money is gone (it comes back at a
known instant, so `Retry-After` is a real clock), `budget_pressure` when the
money is merely claimed by requests in flight (it comes back when they settle,
seconds from now, and rendering that as the midnight reset would be false).
"""

import calendar
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_qa.api.conditions import spec_for
from rag_qa.api.errors import BudgetExhausted, BudgetPressure

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

# How long a caller is told to wait after a reservation-pressure refusal. The
# condition clears when the requests in flight settle — a generation, not a
# window — so there is no boundary to count down to and `reset: shortly` is what
# the client is told. This number exists because `Retry-After` still has to be
# some number of seconds; it is deliberately in the range of one generation
# rather than a guess at a queue length.
PRESSURE_RETRY_AFTER_SECONDS = 5


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


@dataclass(frozen=True)
class BudgetSnapshot:
    """What `/metrics` may publish about spend without touching the database.

    `age_seconds` is not decoration. The cache refreshes only when `check()`
    runs, so an idle replica's snapshot ages without bound — and other replicas
    may be spending the shared budget the whole time. The age is what lets an
    operator tell "headroom is $4" from "headroom was $4, forty minutes ago".

    `remaining` deliberately keeps its original meaning — ceiling minus money
    actually spent — and `reserved` is published beside it rather than subtracted
    from it. Folding reservations into `remaining` would silently change what an
    existing alert threshold means, which is a quiet way to make a dashboard lie;
    an operator who wants committed headroom subtracts the two series, and one
    who wants spend reads the one that has always meant spend.
    """

    remaining: dict[str, Decimal]
    age_seconds: float
    reserved: Decimal = Decimal("0")


class Reservation:
    """Headroom claimed for one provider call, released however the call ends.

    **The only correct use is `try: ... finally: release()`**, with `settle()` on
    the success path inside it. `release()` after `settle()` is a no-op, which is
    what lets the `finally` be unconditional — and an unconditional `finally` is
    the only shape that covers the paths nobody enumerated: a provider exception,
    a translated `ApiError`, a cancellation when the client disconnects, a stream
    that dies after its first frame. A reservation leaked on one of those is not
    a small bug: it is permanent, invisible, and makes the replica refuse traffic
    it has the budget for until the process restarts.

    `settle()` records the cost **unconditionally** and gives the claim back
    **once**. The asymmetry is deliberate: double-counting spend refuses requests
    early, while losing spend lets the ceiling be exceeded, and only one of those
    two failures is safe to prefer.
    """

    __slots__ = ("_discharge", "_open", "_record", "amount")

    def __init__(
        self,
        *,
        amount: Decimal,
        record: Callable[[Decimal], None],
        discharge: Callable[[Decimal], None],
    ) -> None:
        self.amount = amount
        self._record = record
        self._discharge = discharge
        self._open = True

    def settle(self, cost_usd: Decimal) -> None:
        """Replace the reserved worst case with what the answer actually cost."""
        self._record(cost_usd)
        self.release()

    def release(self) -> None:
        """Give the claim back. Idempotent, so a `finally` can be unconditional."""
        if self._open:
            self._open = False
            self._discharge(self.amount)


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
        # Worst-case cost of every provider call this replica has admitted and
        # not yet seen return. Unlike `_local_delta` this survives a refresh:
        # a refreshed `query_log` total contains everything recorded and nothing
        # reserved, because a reserved request has not written its row yet.
        self._reserved = Decimal("0")
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

    @property
    def reserved(self) -> Decimal:
        """Worst-case cost committed to provider calls still in flight."""
        return self._reserved

    @property
    def recorded(self) -> Decimal:
        """Spend counted since the last refresh — money gone, not money claimed."""
        return self._local_delta

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
        """Raise BudgetExhausted if either ceiling has been reached.

        Reservations are deliberately **not** counted here. This runs before
        retrieval, where the cheapest possible shed lives, and it answers one
        question: is the money gone? Whether the remaining money is already
        claimed is a different question with a different answer for the caller,
        and it cannot be asked yet — the prompt does not exist until retrieval
        has run, so neither does the amount to claim.
        """
        if not self.enabled:
            return
        now = await self._current(now=self._now())
        self._enforce(now, counting_reservations=False)

    async def reserve(self, amount: Decimal) -> Reservation:
        """Claim `amount` of headroom for one provider call.

        Called after retrieval and before generation, so `amount` can be a true
        upper bound on *this* request rather than a guess about a typical one.

        **The incoming reservation is not counted against itself.** The trip
        condition reads what is already spent plus what is already claimed; the
        request being admitted is added afterwards. Counting it would mean a
        ceiling smaller than one worst-case query refuses every request forever —
        an outage manufactured by the guard rather than by the budget — and it
        would do so most readily on exactly the small budgets this is for. What
        the exclusion leaves is the single-query overshoot that check-before-spend
        has always had and cannot not have: the bound is `ceiling + one worst
        case`, which is computable from configuration and does not grow with
        traffic.
        """
        if not self.enabled:
            return self._reservation(Decimal("0"))
        now = await self._current(now=self._now())
        return self._admit(now, amount)

    def _admit(self, now: datetime, amount: Decimal) -> Reservation:
        """Test the ceilings and take the claim — **synchronously, and that is
        the enforcement rather than a convention.**

        The bound `ceiling + one worst-case query` holds only if reading
        `recorded + outstanding` and adding the new claim happen with no
        suspension point between them. Today that would be true of an `async`
        version too, by cooperative scheduling — nothing here awaits. But
        "nothing here awaits" is a property of the current lines, not of the
        function, and it is one `await` from being false: a log call made
        asynchronous, a metric shipped over the network, a database read for a
        per-key limit. Fifty coroutines would then pass the same stale
        comparison and admit together, which is the in-flight blind spot this
        whole mechanism exists to close, rebuilt inside the fix.

        A `def` cannot await. So the critical section cannot acquire a
        suspension point without changing this signature and its caller, which
        is a change a reviewer sees. Same argument as `snapshot()`, applied to
        the other property that silently depends on not yielding.

        The two checks are ordered: money already spent is `budget_exhausted`
        and resets at a known instant; money merely claimed is `budget_pressure`
        and resets when those calls return. The other order would label an
        exhausted budget as transient pressure and promise a retry that cannot
        succeed until midnight.
        """
        self._enforce(now, counting_reservations=False)
        self._enforce(now, counting_reservations=True)
        self._reserved += amount
        return self._reservation(amount)

    def _reservation(self, amount: Decimal) -> Reservation:
        """Bound methods rather than a back-reference, so a `Reservation` can
        settle and release without reaching into the guard's internals."""
        return Reservation(amount=amount, record=self.record, discharge=self._discharge)

    async def _current(self, *, now: datetime) -> datetime:
        """Refresh the cached totals if they are stale, and hand `now` back."""
        assert self._session_factory is not None
        day, month = utc_day_start(now), utc_month_start(now)
        stale = (
            self._refreshed_at is None
            or self._cached_day != day
            or self._cached_month != month
            or (self._monotonic() - self._refreshed_at) >= self._refresh_seconds
        )
        if stale:
            await self._refresh(day, month)
        return now

    def _enforce(self, now: datetime, *, counting_reservations: bool) -> None:
        committed = self._local_delta + (self._reserved if counting_reservations else Decimal("0"))

        # The monthly ceiling is checked first: when both are exhausted, telling
        # a visitor "resets at midnight" would be false, and a Retry-After that
        # expires into another 503 is worse than an honest long one.
        if self._monthly_limit is not None and self._month_total + committed >= self._monthly_limit:
            self._trip(
                ceiling="monthly",
                limit=self._monthly_limit,
                spent=self._month_total + committed,
                origin="",
                resets_at=next_utc_month_start(now),
                retry_after=seconds_until_utc_month_end(now),
                pressure=counting_reservations,
            )

        daily_limit, origin = self._daily_shape(now)
        if daily_limit is not None and self._day_total + committed >= daily_limit:
            self._trip(
                ceiling="daily",
                limit=daily_limit,
                spent=self._day_total + committed,
                origin=origin,
                resets_at=utc_day_start(now) + timedelta(days=1),
                retry_after=seconds_until_utc_midnight(now),
                pressure=counting_reservations,
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
        pressure: bool,
    ) -> None:
        """Figures to the log, a figure-free message to the caller.

        The ceiling, the override, the derived value, and the running total are
        exactly the cost meter SPEC-006 KD-8 refuses to publish — an error body
        naming them hands a caller a live progress bar toward draining the
        budget, and is a side channel around the admin scope on `/metrics`. The
        caller is told *that* the demo is not answering and *when* it resumes,
        which is everything they can act on.

        **The pressure branch says less, and that is the honest amount to say.**
        The budget is not spent, so `resets_at` describes nothing the caller is
        waiting for: the condition clears when the calls in flight return. The
        message carries no instant, `Retry-After` is a few seconds rather than a
        countdown to midnight, and `reset: shortly` (from `CONDITIONS`) tells a
        client not to render a clock at all. The operator still gets the reset
        instant in the log record, because for *them* it is context.
        """
        if pressure:
            logger.warning(
                "spend ceiling committed by requests in flight",
                extra={
                    "ceiling": ceiling,
                    "limit_usd": str(limit),
                    "spent_usd": str(self._month_total + self._local_delta),
                    "committed_usd": str(spent),
                    "reserved_usd": str(self._reserved),
                    "origin": origin.strip() or "configured",
                    "resets_at": resets_at.isoformat(),
                },
            )
            raise BudgetPressure(
                spec_for("budget_pressure").public_message,
                retry_after=PRESSURE_RETRY_AFTER_SECONDS,
                ceiling=ceiling,
            )

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

    def snapshot(self, now: datetime) -> "BudgetSnapshot | None":
        """Headroom from the cached totals, or None if there is nothing cached.

        **Strictly cache-only, and synchronous so it cannot become otherwise.**
        `/metrics` calls this, and SPEC-006 Key decision 9 requires a scrape to
        open no connection: the budget refresh is deliberately outside
        `RESERVED_CONNECTIONS` (Key decision 10), so a monitor scraping every 15s
        would contend for exactly the connections the semaphore's divisor
        protects. A `def` rather than an `async def` is the enforcement — a
        synchronous method cannot await a session, so no future edit can quietly
        make a scrape query without changing the signature and every caller.

        **Returning None before the first refresh is the point.** The totals
        start at zero, so a naive reading would publish the full ceiling as
        headroom on a fresh replica — announcing "plenty of budget" at the exact
        moment the process knows least, and in the direction that gets acted on.
        No snapshot is honest; a wrong number is not.
        """
        if self._refreshed_at is None:
            return None
        headroom: dict[str, Decimal] = {}
        daily_limit, _ = self._daily_shape(now)
        if daily_limit is not None:
            headroom["daily"] = max(Decimal("0"), daily_limit - self._day_total - self._local_delta)
        if self._monthly_limit is not None:
            headroom["monthly"] = max(
                Decimal("0"), self._monthly_limit - self._month_total - self._local_delta
            )
        return BudgetSnapshot(
            remaining=headroom,
            age_seconds=max(0.0, self._monotonic() - self._refreshed_at),
            reserved=self._reserved,
        )

    def record(self, cost_usd: Decimal) -> None:
        """Count spend that has not reached query_log's cached totals yet."""
        self._local_delta += cost_usd

    def _discharge(self, amount: Decimal) -> None:
        """Give back a claim. Never clamped at zero: a negative total would mean
        a reservation was released twice, and clamping would hide the arithmetic
        error that produced it behind a plausible-looking number."""
        self._reserved -= amount

    async def _refresh(self, day: datetime, month: datetime) -> None:
        assert self._session_factory is not None
        async with self._session_factory() as session:
            row = (await session.execute(_SPEND_WINDOWS, {"day": day, "month": month})).one()
        self._day_total = Decimal(str(row.day_total))
        self._month_total = Decimal(str(row.month_total))
        # The recorded delta is cleared and the reservations are not, and the
        # asymmetry is the whole correctness of this line. Everything recorded
        # has already written its `query_log` row, so the freshly-read total
        # contains it and keeping the delta would count it twice. Nothing
        # reserved has written a row yet — that is what "reserved" means — so
        # clearing it would forget every call in flight and re-open the exact
        # blind spot reservations exist to close.
        self._local_delta = Decimal("0")
        self._cached_day = day
        self._cached_month = month
        self._refreshed_at = self._monotonic()
        self.refresh_count += 1

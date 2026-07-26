"""Per-identity token pricing (SPEC-005 KD-10, KD-16).

Rates are data, not comments: the Sonnet 5 introductory rate expires on a date,
and a comment cannot switch the rate on 2026-09-01. Each row carries the date it
was verified and the source it came from, so re-verification is mechanical.

An identity absent from the table raises at client construction. Recording cost 0
for an unpriced model would write a falsehood into the one column whose entire
purpose is cost tracking.

Two entry points, and the distinction matters (KD-16):

- `compute_cost` prices a request that is happening **now** — the live path.
- `recompute_cost` prices a request that **already happened**, from its
  `query_log.created_at`. A log spanning 2026-09-01 legitimately holds rows at
  two Sonnet 5 rates; repricing them all at today's rate is simply wrong.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from rag_qa.generation.types import UnknownModelError

# Per-provider pricing pages. An unpriced model's error message names the page
# the reader actually has to check — pointing an OpenAI swap at Anthropic's
# pricing page is guidance that fails on the fresh clone it exists to help.
PRICING_SOURCES: dict[str, str] = {
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
    "openai": "https://platform.openai.com/docs/pricing",
}
PRICING_SOURCE = PRICING_SOURCES["anthropic"]
PRICING_VERIFIED_ON = date(2026, 7, 26)


def pricing_source_for(identity: str) -> str:
    """The published pricing page a rate row for `identity` must be verified against."""
    provider = identity.split(":", 1)[0]
    return PRICING_SOURCES.get(provider, "the provider's published pricing page")


_MILLION = Decimal(1_000_000)
_CENTS = Decimal("0.000001")  # query_log.cost_usd is numeric(10,6)


@dataclass(frozen=True)
class Rate:
    """USD per million tokens, valid over a closed date interval."""

    input_per_mtok: Decimal
    output_per_mtok: Decimal
    effective_from: date
    effective_until: date | None  # None = open-ended
    verified_on: date
    source: str

    def covers(self, when: date) -> bool:
        if when < self.effective_from:
            return False
        return self.effective_until is None or when <= self.effective_until


def _anthropic_rate(
    input_per_mtok: str,
    output_per_mtok: str,
    effective_from: date,
    effective_until: date | None = None,
) -> Rate:
    return Rate(
        input_per_mtok=Decimal(input_per_mtok),
        output_per_mtok=Decimal(output_per_mtok),
        effective_from=effective_from,
        effective_until=effective_until,
        verified_on=PRICING_VERIFIED_ON,
        source=PRICING_SOURCE,
    )


# Anthropic rates verified against PRICING_SOURCE on PRICING_VERIFIED_ON.
#
# No OpenAI rows ship, and that is deliberate rather than an omission: no spec
# has chosen an OpenAI model, and inventing one here to make a table row would
# make an unmeasured model choice by implementation. Until a row is added —
# verified against PRICING_SOURCES["openai"] — OpenAIClient raises
# UnknownModelError at construction, which is exactly the behavior KD-10
# specifies for an unpriced model. The README quickstart says so out loud, so a
# fresh clone learns it before the exception (KD-10, amended 2026-07-26).
PRICING: dict[str, tuple[Rate, ...]] = {
    "anthropic:claude-sonnet-5": (
        # Introductory pricing through 2026-08-31.
        _anthropic_rate("2", "10", date(2025, 1, 1), date(2026, 8, 31)),
        _anthropic_rate("3", "15", date(2026, 9, 1)),
    ),
    "anthropic:claude-opus-5": (_anthropic_rate("5", "25", date(2025, 1, 1)),),
    "anthropic:claude-haiku-4-5": (_anthropic_rate("1", "5", date(2025, 1, 1)),),
}


def resolve_rate(identity: str, when: date | None = None) -> Rate:
    """The rate in force for `identity` on `when` (default: today, UTC)."""
    rates = PRICING.get(identity)
    if not rates:
        raise UnknownModelError(
            f"no pricing for generator identity {identity!r}; add a rate row to "
            f"rag_qa.generation.pricing.PRICING verified against "
            f"{pricing_source_for(identity)} "
            "(cost_usd is not-null and must never be guessed)"
        )
    effective_on = when or datetime.now(UTC).date()
    for rate in rates:
        if rate.covers(effective_on):
            return rate
    raise UnknownModelError(f"no pricing for {identity!r} effective {effective_on.isoformat()}")


def compute_cost(
    identity: str,
    prompt_tokens: int,
    completion_tokens: int,
    when: date | None = None,
) -> Decimal:
    """Point-in-time cost. SPEC-002 stores this per request because token counts
    alone cannot reconstruct cost after a price change."""
    rate = resolve_rate(identity, when)
    cost = (
        Decimal(prompt_tokens) * rate.input_per_mtok
        + Decimal(completion_tokens) * rate.output_per_mtok
    ) / _MILLION
    return cost.quantize(_CENTS)


def recompute_cost(
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    created_at: datetime,
) -> Decimal:
    """Cost of an **already-logged** request, at the rate in force when it ran.

    `created_at` is the authority, never `today` (KD-16). `query_log` stores
    `provider` and `model` separately (KD-8), so the identity is reassembled
    here rather than stored a third time.

    A naive datetime is rejected rather than assumed UTC: `query_log.created_at`
    is `timestamptz`, and silently reading one in local time shifts the date by
    up to a day — which on 2026-08-31 picks the wrong Sonnet 5 rate.
    """
    if created_at.tzinfo is None:
        raise ValueError(
            "created_at must be timezone-aware (query_log.created_at is timestamptz); "
            "a naive value would be interpreted in local time and can resolve to the "
            "wrong side of a rate change"
        )
    return compute_cost(
        f"{provider}:{model}",
        prompt_tokens,
        completion_tokens,
        when=created_at.astimezone(UTC).date(),
    )

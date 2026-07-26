"""Per-identity token pricing (SPEC-005 KD-10).

Rates are data, not comments: the Sonnet 5 introductory rate expires on a date,
and a comment cannot switch the rate on 2026-09-01. Each row carries the date it
was verified and the source it came from, so re-verification is mechanical.

An identity absent from the table raises at client construction. Recording cost 0
for an unpriced model would write a falsehood into the one column whose entire
purpose is cost tracking.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from rag_qa.generation.types import UnknownModelError

PRICING_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICING_VERIFIED_ON = date(2026, 7, 26)

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
# No OpenAI rows ship by default, and that is deliberate rather than an omission:
# adding an OpenAI model means adding a rate row verified against OpenAI's own
# pricing page. Until then OpenAIClient raises UnknownModelError at construction,
# which is exactly the behavior KD-10 specifies for an unpriced model.
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
            f"rag_qa.generation.pricing.PRICING verified against {PRICING_SOURCE} "
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

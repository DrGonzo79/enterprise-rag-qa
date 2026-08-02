"""The embedding spend guard's own arithmetic (SPEC-003 AC-14).

No network and no API key: the guard has to be correct in CI, which is the
environment it exists to protect and the one where it can never be exercised
against a real provider.
"""

from decimal import Decimal

import pytest

from embedding_ledger import (
    CEILING_ENV_VAR,
    DEFAULT_CEILING_USD,
    EmbeddingBudgetExceeded,
    EmbeddingLedger,
    report,
    resolve_ceiling,
)
from rag_qa.ingest.pipeline import EMBEDDING_USD_PER_MTOK


def _ledger(ceiling: str = "0.0002") -> EmbeddingLedger:
    return EmbeddingLedger(ceiling_usd=Decimal(ceiling))


def test_cost_is_priced_from_the_same_constant_the_ingest_pipeline_bills_at() -> None:
    """Not a literal. A second copy of the price would let the guard and the
    ingest report disagree about what a token costs, and the guard is the one
    nobody would check."""
    ledger = _ledger()
    ledger.record("t", texts=1, tokens=1_000_000)
    assert ledger.cost_usd == Decimal(str(EMBEDDING_USD_PER_MTOK))


def test_the_ceiling_stops_a_loop_rather_than_reporting_on_one() -> None:
    """The distinction this guard turns on. A session-end total says what the
    runaway cost; `check()` runs *before* each call, so the ceiling is what the
    runaway is allowed to cost — crossed by at most one call's worth."""
    ledger = _ledger("0.0002")
    # 10,000 tokens/call at $0.02/Mtok = $0.0002/call, so the second call is the
    # first one that can see a crossed ceiling.
    ledger.check()
    ledger.record("tests/test_loop.py::test_runaway", texts=1, tokens=10_000)
    ledger.check()  # exactly at the ceiling, not over it
    ledger.record("tests/test_loop.py::test_runaway", texts=1, tokens=10_000)
    with pytest.raises(EmbeddingBudgetExceeded) as caught:
        ledger.check()

    message = str(caught.value)
    assert "tests/test_loop.py::test_runaway" in message, "the failure must name the spender"
    assert "0.0002" in message
    assert "20,000 tokens" in message


def test_spend_is_attributed_to_the_test_that_caused_it() -> None:
    ledger = _ledger()
    ledger.record("a", texts=1, tokens=10)
    ledger.record("b", texts=2, tokens=500)
    ledger.record("a", texts=1, tokens=10)
    assert ledger.by_test() == [("b", 1, 500), ("a", 2, 20)]


def test_the_override_can_only_tighten_the_ceiling() -> None:
    """A knob that loosens a spend guard is worse than no knob: it is the thing
    someone reaches for to make a red build green, and it is in the file where a
    reviewer would go looking for the guard and find it disarmed."""
    assert resolve_ceiling({}) == DEFAULT_CEILING_USD
    assert resolve_ceiling({CEILING_ENV_VAR: "0.00001"}) == Decimal("0.00001")
    assert resolve_ceiling({CEILING_ENV_VAR: "999"}) == DEFAULT_CEILING_USD
    # Malformed does not raise: a bad value in CI must not be what decides
    # whether the guard is armed.
    assert resolve_ceiling({CEILING_ENV_VAR: "banana"}) == DEFAULT_CEILING_USD
    assert resolve_ceiling({CEILING_ENV_VAR: "  "}) == DEFAULT_CEILING_USD


def test_a_run_that_embedded_nothing_reports_nothing() -> None:
    """CI has no API key and makes no real calls; a spend section reading $0.00
    on every run is noise that teaches people to skip the section."""
    assert report(_ledger()) == []
    populated = _ledger()
    populated.record("a", texts=1, tokens=10)
    assert report(populated)
    assert "10 tokens" in "\n".join(report(populated))

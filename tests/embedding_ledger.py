"""Price every real embedding call the test suite makes, and stop it at a ceiling.

**The exposure this closes is visibility, not money.** Measured 2026-08-02, a full
suite run makes 79 real embedding calls totalling 998 tokens — **$0.00002**. What
was missing was not a large bill; it was any bill at all. `query_log.cost_usd` is
generation-only (SPEC-006 Key decision 16), so embedding spend reached no ledger,
no counter, and no ceiling, and a test that looped over `embed` would have run up
a charge that nothing in this repository could observe, let alone bound.

Two mechanisms, and the order matters:

1. **A hard stop inside the call.** The ceiling is checked *before* each call and
   raises once crossed, so a runaway loop stops spending at the ceiling rather
   than being reported after it finished. A guard that only totals up at session
   end tells you what the loop cost; this one decides what it may cost.
2. **A report at session end, always.** Printed on every run that made a real
   call, whether or not the ceiling was near — because the number this file
   exists to expose is worth seeing when it is small, and "small" is a claim that
   decays (SPEC-006 Key decision 16 records the thresholds at which it stops
   being true).

The ceiling may be **lowered** by `RAG_QA_TEST_EMBEDDING_CEILING_USD` and never
raised. A knob that loosens a guard is the failure this project has now written
into three separate configuration decisions; an override that can only tighten is
useful to CI and useless to someone trying to make a red build go green.
"""

import os
from dataclasses import dataclass, field
from decimal import Decimal

from rag_qa.ingest.pipeline import EMBEDDING_USD_PER_MTOK

# 10x the measured cost of a full run (998 tokens, $0.00002 on 2026-08-02).
# $0.0002 is 10,000 tokens: at the suite's measured ~12.6 tokens per smoke query
# that is ~790 query-sized calls against the 79 it makes today, so the real-corpus
# tier would have to grow tenfold before this is anywhere near it -- while a loop
# over chunk-sized texts (~570 tokens) crosses it in under twenty calls.
DEFAULT_CEILING_USD = Decimal("0.0002")
CEILING_ENV_VAR = "RAG_QA_TEST_EMBEDDING_CEILING_USD"

_ENCODING = "cl100k_base"  # what text-embedding-3-small tokenizes with


class EmbeddingBudgetExceeded(AssertionError):
    """Raised from inside `embed` so the offending test is the one that fails.

    An `AssertionError` rather than a custom exception hierarchy: this is a test
    failure, it should read as one in the traceback, and nothing is meant to
    catch it.
    """


@dataclass
class Call:
    test: str
    texts: int
    tokens: int


@dataclass
class EmbeddingLedger:
    ceiling_usd: Decimal
    calls: list[Call] = field(default_factory=list)
    current_test: str = "<collection>"

    @property
    def tokens(self) -> int:
        return sum(call.tokens for call in self.calls)

    @property
    def cost_usd(self) -> Decimal:
        return Decimal(self.tokens) / Decimal(1_000_000) * Decimal(str(EMBEDDING_USD_PER_MTOK))

    def by_test(self) -> list[tuple[str, int, int]]:
        """(test, calls, tokens), most tokens first."""
        totals: dict[str, tuple[int, int]] = {}
        for call in self.calls:
            calls, tokens = totals.get(call.test, (0, 0))
            totals[call.test] = (calls + 1, tokens + call.tokens)
        return sorted(((test, c, t) for test, (c, t) in totals.items()), key=lambda row: -row[2])

    def check(self) -> None:
        """Called before each embedding call, so the ceiling bounds the bill
        rather than describing it."""
        if self.cost_usd > self.ceiling_usd:
            top = self.by_test()[:5]
            spenders = "\n".join(f"      {t:,} tokens in {c} calls  {name}" for name, c, t in top)
            raise EmbeddingBudgetExceeded(
                f"test-suite embedding spend ${self.cost_usd:.8f} exceeds the "
                f"${self.ceiling_usd} ceiling after {len(self.calls)} real calls "
                f"({self.tokens:,} tokens).\n"
                f"    Biggest spenders:\n{spenders}\n"
                f"    A test is making real embedding calls in a loop. Embedding spend "
                f"reaches no ledger and no budget ceiling (SPEC-006 Key decision 16), so "
                f"this guard is the only thing that bounds it."
            )

    def record(self, test: str, texts: int, tokens: int) -> None:
        self.calls.append(Call(test=test, texts=texts, tokens=tokens))


def resolve_ceiling(environ: dict[str, str] | None = None) -> Decimal:
    """The override may only tighten. Anything else is silently ignored rather
    than raising, because a malformed value in CI must not be the thing that
    decides whether a spend guard is armed."""
    raw = (environ if environ is not None else dict(os.environ)).get(CEILING_ENV_VAR, "").strip()
    if not raw:
        return DEFAULT_CEILING_USD
    try:
        requested = Decimal(raw)
    except ArithmeticError:
        return DEFAULT_CEILING_USD
    return min(DEFAULT_CEILING_USD, requested)


def install(ledger: EmbeddingLedger) -> None:
    """Wrap `OpenAIEmbeddingClient.embed` for the whole session.

    Patched on the class rather than injected at each construction site: the
    point is to catch the call site nobody remembered, and an opt-in ledger
    measures only the code that opted in.
    """
    import tiktoken

    from rag_qa.ingest.embedder import OpenAIEmbeddingClient

    if getattr(OpenAIEmbeddingClient.embed, "_ledgered", False):
        return

    encoding = tiktoken.get_encoding(_ENCODING)
    original = OpenAIEmbeddingClient.embed

    async def counted(self: OpenAIEmbeddingClient, texts: list[str]) -> list[list[float]]:
        tokens = sum(len(encoding.encode(text)) for text in texts)
        # Charged before the call and checked before charging, so the ceiling is
        # never crossed by more than one call's worth.
        ledger.check()
        ledger.record(ledger.current_test, len(texts), tokens)
        return await original(self, texts)

    counted._ledgered = True  # type: ignore[attr-defined]
    OpenAIEmbeddingClient.embed = counted  # type: ignore[method-assign]


def report(ledger: EmbeddingLedger) -> list[str]:
    """Lines for the terminal summary. Empty when nothing real was called, so a
    CI run with no API key stays quiet."""
    if not ledger.calls:
        return []
    lines = [
        f"{len(ledger.calls)} real embedding calls, {ledger.tokens:,} tokens, "
        f"${ledger.cost_usd:.8f} (ceiling ${ledger.ceiling_usd})",
    ]
    lines += [f"  {t:,} tokens in {c} calls  {name}" for name, c, t in ledger.by_test()[:5]]
    return lines

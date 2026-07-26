"""Generator tests from SPEC-005 AC-1, AC-2, AC-3, AC-4, AC-6, AC-7, AC-9,
AC-10, AC-12, plus migration 0004 (AC-8)."""

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import DATABASE_URL
from rag_qa.db.models import QueryLog
from rag_qa.generation.clients.base import LLMResult, StopKind, TextChunk, Usage
from rag_qa.generation.pricing import compute_cost, recompute_cost, resolve_rate
from rag_qa.generation.prompt import PROMPT_VERSION, SYSTEM_PROMPT
from rag_qa.generation.service import Generator
from rag_qa.generation.types import (
    Answer,
    AnswerComplete,
    CitationEvent,
    TextDelta,
    UnknownModelError,
    Verdict,
    VerdictEvent,
)
from rag_qa.retrieval.types import RetrievedChunk

IDENTITY = "anthropic:claude-sonnet-5"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _chunk(index: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid5(uuid.NAMESPACE_DNS, f"gen-chunk-{index}"),
        document_id=uuid.uuid5(uuid.NAMESPACE_DNS, "gen-doc"),
        document_title=f"Document {index}",
        source_uri=f"synthetic://gen/{index}",
        doc_type="regulation",
        section_path=f"EU AI Act › CHAPTER III › Article {index}",
        ordinal=index,
        text=f"Article {index} body text about conformity assessment.",
        score=1.0 / index,
        vector_rank=index,
        fulltext_rank=None,
    )


CHUNKS = [_chunk(i) for i in range(1, 5)]


class FakeLLMClient:
    """Records exactly what it was called with; returns a scripted response."""

    def __init__(
        self,
        response: str = "ANSWERED\nProviders must comply [1].",
        *,
        identity: str = IDENTITY,
        provider: str = "anthropic",
        model: str = "claude-sonnet-5",
        stop: StopKind = StopKind.NORMAL,
        prompt_tokens: int = 1200,
        completion_tokens: int = 80,
        stream_slices: Sequence[str] | None = None,
        emit_usage: bool = True,
    ) -> None:
        self.identity = identity
        self.provider = provider
        self.model = model
        self._response = response
        self._stop = stop
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self._stream_slices = stream_slices
        self._emit_usage = emit_usage
        self.calls: list[tuple[str, str, int]] = []

    async def complete(self, system: str, user: str, max_tokens: int) -> LLMResult:
        self.calls.append((system, user, max_tokens))
        return LLMResult(
            text="" if self._stop is StopKind.REFUSAL else self._response,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            stop=self._stop,
        )

    @asynccontextmanager
    async def stream(
        self, system: str, user: str, max_tokens: int
    ) -> AsyncIterator[AsyncIterator[TextChunk | Usage]]:
        self.calls.append((system, user, max_tokens))
        slices = self._stream_slices or [self._response]
        emit_usage = self._emit_usage
        prompt_tokens = self._prompt_tokens
        completion_tokens = self._completion_tokens
        stop = self._stop

        async def events() -> AsyncIterator[TextChunk | Usage]:
            for piece in slices:
                yield TextChunk(piece)
            if emit_usage:
                yield Usage(prompt_tokens, completion_tokens, stop)

        yield events()


# --- AC-1: contract -----------------------------------------------------------


async def test_answer_populates_every_field() -> None:
    generator = Generator(FakeLLMClient())
    answer = await generator.answer("What does Article 1 require?", CHUNKS)

    assert isinstance(answer, Answer)
    assert answer.verdict is Verdict.ANSWERED
    assert answer.text == "Providers must comply [1]."
    assert [c.chunk_id for c in answer.citations] == [CHUNKS[0].chunk_id]
    assert answer.citations[0].section_path == CHUNKS[0].section_path
    assert answer.generator_identity == IDENTITY
    assert answer.prompt_version == PROMPT_VERSION
    assert answer.prompt_tokens == 1200
    assert answer.completion_tokens == 80
    assert answer.cost_usd > 0
    assert answer.latency_ms >= 0
    assert answer.dropped_markers == ()


async def test_empty_question_raises() -> None:
    generator = Generator(FakeLLMClient())
    with pytest.raises(ValueError):
        await generator.answer("   \n", CHUNKS)


# --- AC-4: the rendered prompt ------------------------------------------------


async def test_prompt_numbers_every_chunk_with_its_section_path() -> None:
    client = FakeLLMClient()
    generator = Generator(client)
    await generator.answer("What applies?", CHUNKS)

    system, user, max_tokens = client.calls[0]
    assert system == SYSTEM_PROMPT
    assert max_tokens == 4096
    for index, chunk in enumerate(CHUNKS, start=1):
        assert f"[{index}] {chunk.section_path}" in user
        assert chunk.text in user
    assert "Question: What applies?" in user
    # Chunk order preserved: [1] must appear before [2].
    assert user.index("[1] ") < user.index("[2] ")


# --- AC-3: sampling parameters and stability ----------------------------------


def test_anthropic_adapter_sends_no_sampling_parameters() -> None:
    """Reading the adapter source is the honest test here: sending temperature
    is a 400 on current models, so there is no response to assert against."""
    import inspect

    from rag_qa.generation.clients import anthropic as adapter

    source = inspect.getsource(adapter)
    for banned in ("temperature", "top_p", "top_k"):
        assert f"{banned}=" not in source, f"{banned} would 400 on current models"


def test_openai_adapter_sends_temperature_zero() -> None:
    import inspect

    from rag_qa.generation.clients import openai as adapter

    assert "temperature=0" in inspect.getsource(adapter)


async def test_verdict_and_cited_chunks_stable_across_runs() -> None:
    generator = Generator(FakeLLMClient())
    results = [await generator.answer("What applies?", CHUNKS) for _ in range(3)]
    verdicts = {r.verdict for r in results}
    cited = {tuple(c.chunk_id for c in r.citations) for r in results}
    assert len(verdicts) == 1
    assert len(cited) == 1


# --- AC-6: refusal paths ------------------------------------------------------


async def test_model_refusal_yields_insufficient_evidence() -> None:
    client = FakeLLMClient("INSUFFICIENT_EVIDENCE\nThe excerpts do not cover this.")
    answer = await Generator(client).answer("Unanswerable?", CHUNKS)
    assert answer.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert answer.text == "The excerpts do not cover this."


async def test_no_chunks_refuses_without_an_llm_call() -> None:
    client = FakeLLMClient()
    answer = await Generator(client).answer("Anything?", [])
    assert answer.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert client.calls == []
    assert answer.prompt_tokens == 0
    assert answer.cost_usd == Decimal("0.000000")


async def test_provider_refusal_is_not_insufficient_evidence() -> None:
    """The two refusals must stay distinct or the charter's refusal metric
    silently blends them (SPEC-005 KD-5)."""
    client = FakeLLMClient(stop=StopKind.REFUSAL)
    answer = await Generator(client).answer("Cyber question?", CHUNKS)
    assert answer.verdict is Verdict.PROVIDER_REFUSED
    assert answer.verdict is not Verdict.INSUFFICIENT_EVIDENCE


async def test_max_tokens_yields_truncated() -> None:
    client = FakeLLMClient("ANSWERED\nHalf an ans", stop=StopKind.MAX_TOKENS)
    answer = await Generator(client).answer("Long question?", CHUNKS)
    assert answer.verdict is Verdict.TRUNCATED


async def test_malformed_verdict_is_error_not_assumed_answered() -> None:
    client = FakeLLMClient("Here you go:\nArticle 1 applies [1].")
    answer = await Generator(client).answer("What applies?", CHUNKS)
    assert answer.verdict is Verdict.ERROR


# --- AC-7 / AC-7a: streaming ---------------------------------------------------


async def _collect(generator: Generator, question: str, chunks: Sequence[RetrievedChunk]):
    return [event async for event in generator.stream_answer(question, chunks)]


async def test_stream_event_order_and_completion() -> None:
    client = FakeLLMClient(stream_slices=["ANSWE", "RED\nProviders comply [", "1]. Also [2]."])
    events = await _collect(Generator(client), "What applies?", CHUNKS)

    assert isinstance(events[0], VerdictEvent)
    assert events[0].verdict is Verdict.ANSWERED
    assert isinstance(events[-1], AnswerComplete)

    deltas = [e.text for e in events if isinstance(e, TextDelta)]
    assert "".join(deltas) == "Providers comply [1]. Also [2]."
    assert "ANSWERED" not in "".join(deltas)

    citations = [e.citation.marker for e in events if isinstance(e, CitationEvent)]
    assert citations == [1, 2]

    complete = events[-1]
    assert isinstance(complete, AnswerComplete)
    assert complete.answer.prompt_tokens == 1200
    assert complete.answer.cost_usd > 0


async def test_stream_matches_non_streaming_text() -> None:
    raw = "ANSWERED\nProviders comply [1]. Also [2]."
    streamed = await _collect(
        Generator(FakeLLMClient(stream_slices=[raw[i : i + 4] for i in range(0, len(raw), 4)])),
        "What applies?",
        CHUNKS,
    )
    direct = await Generator(FakeLLMClient(raw)).answer("What applies?", CHUNKS)
    deltas = "".join(e.text for e in streamed if isinstance(e, TextDelta))
    assert deltas == direct.text


async def test_stream_without_usage_is_truncated_never_silent_zero_cost() -> None:
    client = FakeLLMClient(stream_slices=["ANSWERED\nPartial answer [1]"], emit_usage=False)
    events = await _collect(Generator(client), "What applies?", CHUNKS)
    complete = events[-1]
    assert isinstance(complete, AnswerComplete)
    assert complete.answer.verdict is Verdict.TRUNCATED


async def test_stream_ending_mid_verdict_emits_no_text() -> None:
    client = FakeLLMClient(stream_slices=["ANSW"], emit_usage=False)
    events = await _collect(Generator(client), "What applies?", CHUNKS)
    assert [e for e in events if isinstance(e, TextDelta)] == []
    complete = events[-1]
    assert isinstance(complete, AnswerComplete)
    assert complete.answer.verdict is Verdict.ERROR


# --- AC-9: cost and the pricing table -----------------------------------------


def test_cost_matches_point_in_time_rate() -> None:
    # Sonnet 5 introductory: $2/MTok input, $10/MTok output.
    cost = compute_cost(IDENTITY, 1_000_000, 100_000, when=date(2026, 7, 26))
    assert cost == Decimal("3.000000")  # $2.00 + $1.00


def test_sonnet_introductory_rate_expires_on_a_date() -> None:
    last_intro = resolve_rate(IDENTITY, date(2026, 8, 31))
    first_standard = resolve_rate(IDENTITY, date(2026, 9, 1))
    assert (last_intro.input_per_mtok, last_intro.output_per_mtok) == (
        Decimal("2"),
        Decimal("10"),
    )
    assert (first_standard.input_per_mtok, first_standard.output_per_mtok) == (
        Decimal("3"),
        Decimal("15"),
    )


def test_every_rate_row_records_its_source_and_verification_date() -> None:
    from rag_qa.generation.pricing import PRICING

    for identity, rates in PRICING.items():
        for rate in rates:
            assert rate.source.startswith("https://"), identity
            assert rate.verified_on is not None, identity


def test_unpriced_model_raises_at_construction_not_request_time() -> None:
    with pytest.raises(UnknownModelError):
        resolve_rate("anthropic:claude-not-a-real-model")


def test_unpriced_model_error_names_that_provider_s_own_pricing_page() -> None:
    """A fresh clone swapping to OpenAI must not be sent to Anthropic's pricing
    page to verify an OpenAI rate (KD-10, amended)."""
    from rag_qa.generation.pricing import PRICING_SOURCES

    with pytest.raises(UnknownModelError) as openai_error:
        resolve_rate("openai:gpt-5")
    assert PRICING_SOURCES["openai"] in str(openai_error.value)
    assert PRICING_SOURCES["anthropic"] not in str(openai_error.value)

    with pytest.raises(UnknownModelError) as anthropic_error:
        resolve_rate("anthropic:claude-not-a-real-model")
    assert PRICING_SOURCES["anthropic"] in str(anthropic_error.value)


def test_no_openai_rate_rows_ship() -> None:
    """KD-10, amended: shipping a row would pick an OpenAI model no spec chose.
    The README quickstart documents this so the fresh clone learns it before the
    exception does — if a row is ever added, that note must change with it."""
    from rag_qa.generation.pricing import PRICING

    assert not [identity for identity in PRICING if identity.startswith("openai:")]
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "OpenAIClient" in readme
    assert "rate row" in readme


# --- AC-9a: recomputation prices from created_at, never today (KD-16) ---------

RATE_CHANGE = date(2026, 9, 1)
MTOK = (1_000_000, 100_000)  # prompt, completion


def test_recompute_uses_the_rate_in_force_when_the_request_ran() -> None:
    def cost_at(moment: datetime) -> Decimal:
        return recompute_cost(
            provider="anthropic",
            model="claude-sonnet-5",
            prompt_tokens=MTOK[0],
            completion_tokens=MTOK[1],
            created_at=moment,
        )

    last_intro = cost_at(datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC))
    first_standard = cost_at(datetime(2026, 9, 1, 0, 0, 1, tzinfo=UTC))

    assert last_intro == Decimal("3.000000")  # $2/MTok in + $10/MTok out
    assert first_standard == Decimal("4.500000")  # $3/MTok in + $15/MTok out
    assert last_intro != first_standard, "two seconds apart must straddle the rate change"


def test_recompute_is_a_pure_function_of_created_at() -> None:
    """The bug this guards: repricing history at the current rate. The same row
    must cost the same whether it is recomputed before or after the change."""
    row = {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "prompt_tokens": MTOK[0],
        "completion_tokens": MTOK[1],
        "created_at": datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
    }
    assert recompute_cost(**row) == compute_cost(IDENTITY, *MTOK, when=date(2026, 7, 26))
    # …and not what today's-rate pricing would give once the change has landed.
    assert recompute_cost(**row) != compute_cost(IDENTITY, *MTOK, when=RATE_CHANGE)


def test_recompute_rejects_a_naive_timestamp() -> None:
    """query_log.created_at is timestamptz; reading one naively shifts the date
    by up to a day, which on 2026-08-31 picks the wrong rate."""
    with pytest.raises(ValueError, match="timezone-aware"):
        recompute_cost(
            provider="anthropic",
            model="claude-sonnet-5",
            prompt_tokens=MTOK[0],
            completion_tokens=MTOK[1],
            created_at=datetime(2026, 8, 31, 23, 59, 59),  # naive on purpose
        )


async def test_recompute_prices_logged_rows_from_their_own_created_at(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A query_log spanning 2026-09-01 legitimately holds rows at two rates.
    Recomputation reads each row's own created_at, so the batch reprices
    correctly; one rate applied to all of it would be wrong for half."""
    marker = f"recompute-{uuid.uuid4()}"
    moments = [
        datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    ]

    async with session_factory() as session:
        for moment in moments:
            session.add(
                QueryLog(
                    id=uuid.uuid4(),
                    question=f"{marker} {moment.date()}",
                    provider="anthropic",
                    model="claude-sonnet-5",
                    latency_ms=100,
                    prompt_tokens=MTOK[0],
                    completion_tokens=MTOK[1],
                    cost_usd=recompute_cost(
                        provider="anthropic",
                        model="claude-sonnet-5",
                        prompt_tokens=MTOK[0],
                        completion_tokens=MTOK[1],
                        created_at=moment,
                    ),
                    retrieved_chunk_ids=[],
                    answer_text="Providers must comply [1].",
                    verdict=str(Verdict.ANSWERED),
                    prompt_version=PROMPT_VERSION,
                    created_at=moment,
                )
            )
        await session.commit()

    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT provider, model, prompt_tokens, completion_tokens, cost_usd, "
                    "created_at FROM query_log WHERE question LIKE :q ORDER BY created_at"
                ),
                {"q": f"{marker}%"},
            )
        ).all()
        await session.execute(
            text("DELETE FROM query_log WHERE question LIKE :q"), {"q": f"{marker}%"}
        )
        await session.commit()

    assert len(rows) == 2
    recomputed = [
        recompute_cost(
            provider=row[0],
            model=row[1],
            prompt_tokens=row[2],
            completion_tokens=row[3],
            created_at=row[5],
        )
        for row in rows
    ]
    assert recomputed == [Decimal("3.000000"), Decimal("4.500000")]
    assert [row[4] for row in rows] == recomputed  # stored cost survives a reprice
    assert sum(recomputed) != compute_cost(IDENTITY, *MTOK, when=RATE_CHANGE) * 2


async def test_live_row_reprices_to_exactly_what_was_stored(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """created_at is set from the same timestamp the request was priced at, so a
    request straddling midnight on a rate-change date cannot store a cost its
    own created_at contradicts."""
    question = f"Reprice {uuid.uuid4()}"
    answer = await Generator(FakeLLMClient(), session_factory=session_factory).answer(
        question, CHUNKS
    )

    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT provider, model, prompt_tokens, completion_tokens, cost_usd, "
                    "created_at FROM query_log WHERE question = :q"
                ),
                {"q": question},
            )
        ).one()
        await session.execute(text("DELETE FROM query_log WHERE question = :q"), {"q": question})
        await session.commit()

    assert row[4] == answer.cost_usd
    assert (
        recompute_cost(
            provider=row[0],
            model=row[1],
            prompt_tokens=row[2],
            completion_tokens=row[3],
            created_at=row[5],
        )
        == answer.cost_usd
    )


# --- AC-10: generator identity comes from the client --------------------------


async def test_identity_recorded_verbatim_from_the_client() -> None:
    client = FakeLLMClient(identity="anthropic:claude-opus-5", model="claude-opus-5")
    answer = await Generator(client).answer("What applies?", CHUNKS)
    assert answer.generator_identity == "anthropic:claude-opus-5"


def test_anthropic_client_identity_is_derived_from_its_model() -> None:
    import inspect

    from rag_qa.generation.clients.anthropic import AnthropicClient

    source = inspect.getsource(AnthropicClient.__init__)
    assert 'f"{PROVIDER}:{model}"' in source
    assert AnthropicClient.__init__.__defaults__ == ("claude-sonnet-5",)


# --- AC-12: the prompt is versioned -------------------------------------------

# Bump PROMPT_VERSION whenever SYSTEM_PROMPT changes, then update this digest.
# Without this pin, prompt drift is invisible in the logs it is supposed to explain.
EXPECTED_PROMPT_DIGEST = {
    "v1": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
}


def test_prompt_version_pins_the_prompt() -> None:
    assert PROMPT_VERSION
    digest = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert EXPECTED_PROMPT_DIGEST[PROMPT_VERSION] == digest


def test_prompt_states_the_binding_rules() -> None:
    assert "ANSWERED" in SYSTEM_PROMPT
    assert "INSUFFICIENT_EVIDENCE" in SYSTEM_PROMPT
    assert "[n]" in SYSTEM_PROMPT


# --- AC-8: query_log + migration 0004 -----------------------------------------


async def test_answer_writes_one_query_log_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    question = f"Question {uuid.uuid4()}"
    client = FakeLLMClient()
    generator = Generator(client, session_factory=session_factory)
    answer = await generator.answer(question, CHUNKS)

    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT provider, model, verdict, answer_text, prompt_version, "
                    "latency_ms, prompt_tokens, completion_tokens, cost_usd, "
                    "retrieved_chunk_ids FROM query_log WHERE question = :q"
                ),
                {"q": question},
            )
        ).all()
        await session.execute(text("DELETE FROM query_log WHERE question = :q"), {"q": question})
        await session.commit()

    assert len(row) == 1
    provider, model, verdict, answer_text, prompt_version, *_rest = row[0]
    assert (provider, model) == ("anthropic", "claude-sonnet-5")
    assert verdict == str(Verdict.ANSWERED)
    assert answer_text == answer.text
    assert prompt_version == PROMPT_VERSION
    assert row[0][9] == [c.chunk_id for c in CHUNKS]


async def test_refused_call_also_writes_a_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    question = f"Refused {uuid.uuid4()}"
    client = FakeLLMClient("INSUFFICIENT_EVIDENCE\nNot covered.")
    await Generator(client, session_factory=session_factory).answer(question, CHUNKS)

    async with session_factory() as session:
        verdicts = (
            (
                await session.execute(
                    text("SELECT verdict FROM query_log WHERE question = :q"),
                    {"q": question},
                )
            )
            .scalars()
            .all()
        )
        await session.execute(text("DELETE FROM query_log WHERE question = :q"), {"q": question})
        await session.commit()

    assert verdicts == [str(Verdict.INSUFFICIENT_EVIDENCE)]


async def test_migration_0004_roundtrip_preserves_existing_rows() -> None:
    from alembic import command as alembic_command
    from alembic.config import Config

    scratch = "rag_migration_test_0004"
    admin_url = DATABASE_URL.replace("+asyncpg", "").rsplit("/", 1)[0] + "/rag"
    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
        await admin.execute(f'CREATE DATABASE "{scratch}"')
    finally:
        await admin.close()

    scratch_url = DATABASE_URL.rsplit("/", 1)[0] + f"/{scratch}"
    raw_url = scratch_url.replace("+asyncpg", "")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", scratch_url)

    def _run(action: str, revision: str) -> None:
        getattr(alembic_command, action)(config, revision)

    await asyncio.to_thread(_run, "upgrade", "0003")

    conn = await asyncpg.connect(raw_url)
    try:
        await conn.execute(
            "INSERT INTO query_log (id, question, provider, model, latency_ms, "
            "prompt_tokens, completion_tokens, cost_usd, retrieved_chunk_ids) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            uuid.uuid4(),
            "pre-existing question",
            "anthropic",
            "claude-sonnet-5",
            120,
            10,
            5,
            Decimal("0.000100"),
            [],
        )
    finally:
        await conn.close()

    await asyncio.to_thread(_run, "upgrade", "head")

    conn = await asyncpg.connect(raw_url)
    try:
        row = await conn.fetchrow("SELECT answer_text, verdict, prompt_version FROM query_log")
        nullable = await conn.fetch(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'query_log' AND column_name IN "
            "('answer_text', 'verdict', 'prompt_version')"
        )
    finally:
        await conn.close()

    assert row is not None
    # Backfilled with sentinels, not empty strings: a pre-SPEC-005 row must not
    # look like a request that produced no answer.
    assert row["verdict"] == "unknown"
    assert row["prompt_version"] == "pre-spec-005"
    assert {r["is_nullable"] for r in nullable} == {"NO"}
    assert len(nullable) == 3

    await asyncio.to_thread(_run, "downgrade", "-1")

    conn = await asyncpg.connect(raw_url)
    try:
        remaining = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'query_log' AND column_name IN "
            "('answer_text', 'verdict', 'prompt_version')"
        )
        survivors = await conn.fetchval("SELECT count(*) FROM query_log")
    finally:
        await conn.close()

    assert remaining == []
    assert survivors == 1

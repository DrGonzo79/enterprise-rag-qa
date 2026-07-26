"""Generator: prompt → provider → parse → query_log (SPEC-005 Interface)."""

import logging
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_qa.db.models import QueryLog
from rag_qa.generation.citations import AnswerParser, chunk_ids, parse_answer
from rag_qa.generation.clients.base import LLMClient, StopKind, TextChunk
from rag_qa.generation.pricing import compute_cost
from rag_qa.generation.prompt import PROMPT_VERSION, SYSTEM_PROMPT, render_context
from rag_qa.generation.types import (
    Answer,
    AnswerComplete,
    AnswerEvent,
    Verdict,
    VerdictEvent,
)
from rag_qa.retrieval.types import RetrievedChunk

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 4096

NO_EVIDENCE_TEXT = (
    "No supporting excerpts were retrieved for this question, so it cannot be "
    "answered from the corpus."
)


def _verdict_for(parsed: Verdict, stop: StopKind) -> Verdict:
    """Provider stop reason outranks the model's own verdict.

    A classifier refusal and a truncated answer are both facts about the response
    that the parsed verdict cannot know about — and PROVIDER_REFUSED must never
    be recorded as INSUFFICIENT_EVIDENCE (KD-5).
    """
    if stop is StopKind.REFUSAL:
        return Verdict.PROVIDER_REFUSED
    if stop is StopKind.MAX_TOKENS:
        return Verdict.TRUNCATED
    return parsed


class Generator:
    def __init__(
        self,
        client: LLMClient,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._client = client
        self._session_factory = session_factory
        self._max_tokens = max_tokens

    # --- non-streaming --------------------------------------------------------

    async def answer(self, question: str, chunks: Sequence[RetrievedChunk]) -> Answer:
        if not question.strip():
            raise ValueError("question is empty or whitespace-only")

        started = time.perf_counter()
        requested_at = datetime.now(UTC)
        if not chunks:
            # An absence, not a threshold: refuse without spending a request.
            return await self._finalize(
                question=question,
                chunks=chunks,
                text=NO_EVIDENCE_TEXT,
                verdict=Verdict.INSUFFICIENT_EVIDENCE,
                citations=(),
                dropped=(),
                prompt_tokens=0,
                completion_tokens=0,
                started=started,
                requested_at=requested_at,
            )

        user = render_context(question, chunks)
        result = await self._client.complete(SYSTEM_PROMPT, user, self._max_tokens)
        verdict, text, citations, dropped = parse_answer(result.text, chunks)

        return await self._finalize(
            question=question,
            chunks=chunks,
            text=text,
            verdict=_verdict_for(verdict, result.stop),
            citations=citations,
            dropped=dropped,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            started=started,
            requested_at=requested_at,
        )

    # --- streaming ------------------------------------------------------------

    async def stream_answer(
        self, question: str, chunks: Sequence[RetrievedChunk]
    ) -> AsyncIterator[AnswerEvent]:
        if not question.strip():
            raise ValueError("question is empty or whitespace-only")

        started = time.perf_counter()
        requested_at = datetime.now(UTC)
        if not chunks:
            yield VerdictEvent(Verdict.INSUFFICIENT_EVIDENCE)
            answer = await self._finalize(
                question=question,
                chunks=chunks,
                text=NO_EVIDENCE_TEXT,
                verdict=Verdict.INSUFFICIENT_EVIDENCE,
                citations=(),
                dropped=(),
                prompt_tokens=0,
                completion_tokens=0,
                started=started,
                requested_at=requested_at,
            )
            yield AnswerComplete(answer)
            return

        user = render_context(question, chunks)
        parser = AnswerParser(chunks)
        prompt_tokens = 0
        completion_tokens = 0
        stop = StopKind.NORMAL
        saw_usage = False

        async with self._client.stream(SYSTEM_PROMPT, user, self._max_tokens) as events:
            async for event in events:
                if isinstance(event, TextChunk):
                    for parsed_event in parser.feed(event.text):
                        yield parsed_event
                else:
                    saw_usage = True
                    prompt_tokens = event.prompt_tokens
                    completion_tokens = event.completion_tokens
                    stop = event.stop
            for parsed_event in parser.finish():
                yield parsed_event

        verdict = _verdict_for(parser.verdict, stop)
        if not saw_usage and verdict is Verdict.ANSWERED:
            # Stream ended without a usage event — a disconnect, not a clean
            # finish. Never report this as a completed answer with zero cost.
            verdict = Verdict.TRUNCATED

        answer = await self._finalize(
            question=question,
            chunks=chunks,
            text=parser.text,
            verdict=verdict,
            citations=parser.citations,
            dropped=parser.dropped_markers,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            started=started,
            requested_at=requested_at,
        )
        yield AnswerComplete(answer)

    # --- shared tail ----------------------------------------------------------

    async def _finalize(
        self,
        *,
        question: str,
        chunks: Sequence[RetrievedChunk],
        text: str,
        verdict: Verdict,
        citations: tuple[object, ...],
        dropped: tuple[int, ...],
        prompt_tokens: int,
        completion_tokens: int,
        started: float,
        requested_at: datetime,
    ) -> Answer:
        # Priced at the request's own timestamp, and that same timestamp is
        # written to query_log.created_at below — so the stored cost and the
        # column that reprices it can never disagree about which side of a rate
        # change the request fell on (KD-16).
        cost = compute_cost(
            self._client.identity, prompt_tokens, completion_tokens, when=requested_at.date()
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        answer = Answer(
            text=text,
            verdict=verdict,
            citations=citations,  # type: ignore[arg-type]
            generator_identity=self._client.identity,
            prompt_version=PROMPT_VERSION,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            dropped_markers=dropped,
        )
        if dropped:
            logger.warning(
                "model emitted %d citation marker(s) outside 1..%d: %s",
                len(dropped),
                len(chunks),
                dropped,
            )
        await self._write_query_log(question, chunks, answer, requested_at)
        return answer

    async def _write_query_log(
        self,
        question: str,
        chunks: Sequence[RetrievedChunk],
        answer: Answer,
        requested_at: datetime,
    ) -> None:
        if self._session_factory is None:
            return
        async with self._session_factory() as session:
            session.add(
                QueryLog(
                    id=uuid.uuid4(),
                    question=question,
                    # provider/model, not a single identity string: query_log is
                    # queried analytically (KD-8). Both derive from the client.
                    provider=self._client.provider,
                    model=self._client.model,
                    latency_ms=answer.latency_ms,
                    prompt_tokens=answer.prompt_tokens,
                    completion_tokens=answer.completion_tokens,
                    cost_usd=Decimal(answer.cost_usd),
                    retrieved_chunk_ids=chunk_ids(chunks),
                    answer_text=answer.text,
                    verdict=str(answer.verdict),
                    prompt_version=answer.prompt_version,
                    # Set explicitly rather than left to the server default: this
                    # is the timestamp cost_usd was priced at, and recompute
                    # reads it back as the authority (KD-16).
                    created_at=requested_at,
                )
            )
            await session.commit()
